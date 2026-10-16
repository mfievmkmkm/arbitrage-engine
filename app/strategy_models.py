"""Fifteen distinct, fail-closed Paper/replay strategy admission models.

Input evidence is explicit and replay-labelled; verification flags are claims of
the source adapter, not cryptographic proof. Never promote an imported snapshot
to LIVE evidence. A scenario is NOT an estimated probability-weighted return.
"""
from decimal import localcontext
from .strategy_math import (D, ONE, ZERO, Book, Invalid, common_quantity, flag,
                            fresh, leg, nonnegative, positive, plain)
from .strategy_catalog import CATALOG


DEFAULTS = dict(currency='USDT', min_net='0.25', safety='0.10', max_notional='100',
                stop_loss='1', max_hold=1200, max_age=10, convergence='0.5',
                trail_arm='0.25', trail_giveback='0.25', max_spread='0.10')


def policy(values=None):
    p = dict(DEFAULTS, **(values or {}))
    for key in ('min_net', 'safety', 'max_notional', 'stop_loss', 'max_hold',
                'max_age', 'trail_arm', 'trail_giveback', 'max_spread', 'convergence'):
        p[key] = nonnegative(p[key])
    if (min(p['max_notional'], p['stop_loss'], p['max_hold'], p['max_age']) <= 0
            or not ZERO < p['convergence'] <= ONE
            or not ZERO < p['trail_giveback'] < ONE or p['max_age'] > 60
            or p['max_hold'] > 366 * 86400 or not isinstance(p['currency'], str)):
        raise Invalid('POLICY_INVALID')
    return p


def _funding(s, books, sides, q, horizon, now):
    perps = {b.id: (b, side) for b, side in zip(books, sides) if b.kind == 'perp'}
    if not perps:
        return ZERO, []
    flag(s, 'funding_calendar_verified')
    coverage = s['funding_coverage_until']
    if any(D(coverage.get(key, 0)) < now + horizon for key in perps):
        raise Invalid('FUNDING_HORIZON_UNKNOWN')
    events, ids, total = [], set(), ZERO
    for event in s['funding_events']:
        key, ts = event['instrument'], D(event['ts'])
        if key not in perps or (key, ts) in ids or ts <= now:
            raise Invalid('FUNDING_EVENT_INVALID')
        ids.add((key, ts))
        rate = D(event['rate'])
        if abs(rate) >= ONE:
            raise Invalid('FUNDING_RATE_INVALID')
        if ts > now + horizon:
            continue
        b, side = perps[key]
        # Notional price is a scenario assumption, explicitly retained in plan.
        price = positive(event['notional_price'])
        flow = q * price * rate * (-ONE if side == 'buy' else ONE)
        total += flow
        events.append(dict(instrument=key, ts=ts, rate=rate, notional_price=price,
                           scenario_cashflow=flow))
    return total, events


def _borrow(s, books, q, hold, now):
    x = s['borrow']
    flag(x, 'verified')
    if (x['instrument'] != books[0].id or D(x['quantity']) < q
            or D(x['available_until']) < now + hold or x.get('recalled') is not False):
        raise Invalid('BORROW_UNAVAILABLE')
    rate = nonnegative(x['daily_rate'])
    return q * books[0].fill(q, 'sell').price * rate * hold / D(86400)


def _constraints(s, books, key, now, hold):
    kinds = [b.kind for b in books]
    expected = {
        'spot_inventory': [('spot', 'spot')], 'perp_convergence': [('perp', 'perp')],
        'perp_funding': [('perp', 'perp')], 'spot_perp': [('spot', 'perp')],
        'reverse_carry': [('spot', 'perp'), ('spot', 'future')],
        'dated_carry': [('spot', 'future')], 'calendar': [('future', 'future')],
        'cex_dex_spot': [('spot', 'dex'), ('dex', 'spot')],
        'dex_perp': [('dex', 'perp')], 'cross_chain': [('dex', 'dex')],
        'options_parity': [('spot', 'put', 'call')],
    }
    if tuple(kinds) not in expected[key]:
        raise Invalid('STRATEGY_LEG_TYPES')
    if (len({b.asset for b in books}) != 1 or len({b.quote for b in books}) != 1
            or len({b.raw['quote_asset_id'] for b in books}) != 1):
        raise Invalid('ASSET_OR_CURRENCY_MISMATCH')
    if len({b.id for b in books}) != len(books):
        raise Invalid('DUPLICATE_INSTRUMENT')
    if key in ('perp_convergence', 'perp_funding', 'spot_inventory') and books[0].venue == books[1].venue:
        raise Invalid('DISTINCT_VENUES_REQUIRED')
    if key == 'cross_chain':
        flag(s, 'cross_chain_equivalence_verified')
        if books[0].raw['chain'] == books[1].raw['chain']:
            raise Invalid('DISTINCT_CHAINS_REQUIRED')
    for b in books:
        if b.kind in ('future', 'call', 'put'):
            flag(b.raw, 'settlement_verified')
            if b.raw.get('settlement_method') != 'cash':
                raise Invalid('DELIVERY_UNSUPPORTED')
            if D(b.raw['expiry']) <= now:
                raise Invalid('EXPIRED_INSTRUMENT')
    if key == 'dated_carry':
        flag(s, 'spot_index_equivalence_verified')
        if hold < D(books[1].raw['expiry']) - now:
            raise Invalid('EXPIRY_OUTSIDE_HORIZON')
    if key == 'calendar':
        if (D(books[0].raw['expiry']) >= D(books[1].raw['expiry'])
                or now + hold >= D(books[0].raw['expiry'])):
            raise Invalid('CALENDAR_EXPIRY_LIMIT')
        flag(s, 'curve_model_verified')
        D(s['target_spread'])
        if s.get('direction','long_near_short_far') not in ('long_near_short_far','short_near_long_far'):
            raise Invalid('CALENDAR_DIRECTION_INVALID')
    if key == 'reverse_carry' and books[1].kind=='future' and now+hold>=D(books[1].raw['expiry']):
        raise Invalid('REVERSE_EXPIRY_REQUIRES_SEPARATE_SETTLEMENT')
    if key == 'options_parity':
        put, call = books[1:]
        if (put.raw.get('option_style') != 'european'
                or call.raw.get('option_style') != 'european'
                or positive(put.raw['strike']) != positive(call.raw['strike'])
                or D(put.raw['expiry']) != D(call.raw['expiry'])
                or hold < D(put.raw['expiry']) - now):
            raise Invalid('OPTION_PARITY_CONTRACT_MISMATCH')
        flag(s, 'spot_index_equivalence_verified')
        flag(call.raw, 'margin_verified')
        positive(call.raw['margin_per_base'])


def _paired(s, p, now):
    key = s['strategy']
    books = [Book(x, now, p['max_age']) for x in s['books']]
    hold = positive(s.get('horizon_seconds', p['max_hold']))
    if hold > p['max_hold']:
        raise Invalid('HOLD_LIMIT')
    _constraints(s, books, key, now, hold)
    if books[0].quote != p['currency']:
        raise Invalid('ACCOUNT_CURRENCY_MISMATCH')
    q = common_quantity(s['quantity'], [b.step for b in books])
    if q <= 0:
        raise Invalid('COMMON_LOT_ZERO')
    sides = ['buy', 'sell']
    if key == 'reverse_carry':
        sides = ['sell', 'buy']
    elif key == 'calendar' and s.get('direction')=='short_near_long_far':
        sides = ['sell','buy']
    elif key == 'options_parity':
        sides = ['buy', 'buy', 'sell']
    legs = [leg(b, side, q) for b, side in zip(books, sides, strict=True)]
    for x in legs:
        if x['entry_cash'] > p['max_notional']:
            raise Invalid('NOTIONAL_LIMIT')
    # Explicit costs, including financing even when the source believes it is zero.
    flag(s, 'costs_verified')
    financing = nonnegative(s['financing_cost'])
    entry_extra = nonnegative(s['entry_extra_cost'])
    exit_extra = nonnegative(s['exit_extra_cost'])
    for b in books:
        if b.kind == 'dex':
            entry_extra += D(b.raw['gas_entry'])
            exit_extra += D(b.raw['gas_exit'])
    fees = sum((x['entry_fee'] + x['exit_fee'] for x in legs), ZERO)
    entry_net_cash = sum((x['entry_cash'] * (ONE if x['side'] == 'sell' else -ONE)
                          for x in legs), ZERO)
    funding, events = _funding(s, books, sides, q, hold, now)
    borrow = _borrow(s, books, q, hold, now) if key == 'reverse_carry' else ZERO
    inventory = CATALOG[key].family == 'inventory'
    claims = []
    capital = ZERO
    for b, x in zip(books, legs):
        is_inventory = x['side'] == 'sell' and b.kind in ('spot', 'dex') and key != 'reverse_carry'
        cash_reserve = x['entry_cash']
        if b.kind == 'call' and x['side'] == 'sell':
            cash_reserve = max(cash_reserve, q*positive(b.raw['margin_per_base']))
        amount = q if is_inventory else cash_reserve + x['entry_fee'] + x['exit_fee']
        claims.append(dict(venue=b.venue, asset=b.asset if is_inventory else b.quote,
                           amount=amount, inventory=is_inventory))
        capital += cash_reserve + x['entry_fee'] + x['exit_fee']
    rebalance = ZERO
    if inventory:
        flag(s['rebalance'], 'verified')
        rebalance = nonnegative(s['rebalance']['cost'])
        if D(s['rebalance']['deadline']) <= now:
            raise Invalid('REBALANCE_DEADLINE')
        # Inventory spread uses TWO fills; settlement/rebalancing is separate.
        fees = sum((x['entry_fee'] for x in legs), ZERO)
        exit_extra = ZERO
        entry_extra = nonnegative(s['entry_extra_cost']) + sum(
            (D(b.raw['gas_entry']) for b in books if b.kind == 'dex'), ZERO)
        gross = entry_net_cash
    elif key == 'options_parity':
        gross = q * positive(books[1].raw['strike']) + entry_net_cash
    elif key == 'calendar':
        gross = entry_net_cash - q * D(s['target_spread']) * (ONE if sides[0]=='buy' else -ONE)
    elif key == 'perp_funding' or (key == 'spot_perp' and s.get('objective') == 'carry'):
        # A pure carry forecast does not assume free basis convergence.
        gross = -nonnegative(s['adverse_basis_cost'])
    elif key == 'dated_carry':
        gross = entry_net_cash
    else:
        gross = entry_net_cash * p['convergence']
    if not inventory and key not in ('dated_carry','options_parity'):
        # Current executable round-trip friction is not a commission and does
        # not disappear when forecasting a partial convergence or flat basis.
        immediate_gross=sum(((x['exit_cash']-x['entry_cash'])*(ONE if x['side']=='buy' else -ONE)
                             for x in legs),ZERO)
        gross+=min(ZERO,immediate_gross)
    if key not in ('options_parity', 'calendar'):
        spread = abs(entry_net_cash) / max(x['entry_cash'] for x in legs)
        if spread > p['max_spread']:
            raise Invalid('SPREAD_LIMIT')
    projected = gross + funding - fees - entry_extra - exit_extra - financing - borrow - rebalance
    required_net = p['min_net'] + p['safety']
    if projected < required_net:
        raise Invalid('SCENARIO_NET_LOW')
    if not inventory:
        immediate = sum(((x['exit_cash']-x['entry_cash'])*(ONE if x['side']=='buy' else -ONE)
                         for x in legs),ZERO)-fees-entry_extra-exit_extra
        if immediate <= -p['stop_loss']:
            raise Invalid('IMMEDIATE_EXIT_LOSS_EXCEEDS_STOP')
    capital += entry_extra + exit_extra + financing + borrow + rebalance + max(ZERO, -funding) + p['safety']
    # Extra cost/margin reserve is held in the first venue's quote account.
    claims.append(dict(venue=books[0].venue, asset=books[0].quote,
                       amount=entry_extra + exit_extra + financing + borrow + rebalance + max(ZERO, -funding) + p['safety'],
                       inventory=False))
    return dict(strategy=key, route=s['route'], ts=now, currency=p['currency'],
                kind='inventory' if inventory else 'pair', quantity=q, legs=legs,
                claims=claims, capital=capital, scenario_net=projected,
                safety=p['safety'], target_net=max(p['min_net'], projected - p['safety']),
                stop_loss=p['stop_loss'], max_hold=hold, trail_arm=p['trail_arm'],
                trail_giveback=p['trail_giveback'], entry_extra=entry_extra,
                exit_extra=exit_extra, financing_reserve=financing,
                borrow_reserve=borrow, rebalance_reserve=rebalance,
                funding_scenario=events, funding_forecast=funding,
                evidence=s, quality='INDICATIVE_PAPER_REPLAY')


def _cycle(s, p, now):
    key = s['strategy']
    flag(s, 'costs_verified')
    books = [Book(x, now, p['max_age']) for x in s['books']]
    if not 2 <= len(books) <= 6:
        raise Invalid('CYCLE_LENGTH')
    identities = {}
    for b in books:
        for symbol, identity in ((b.base, b.asset), (b.quote, b.raw['quote_asset_id'])):
            if symbol in identities and identities[symbol] != identity:
                raise Invalid('CYCLE_TOKEN_IDENTITY_MISMATCH')
            identities[symbol] = identity
    if s['start_asset'] != p['currency']:
        raise Invalid('ACCOUNT_CURRENCY_MISMATCH')
    if key == 'triangle' and (len(books) < 3 or any(b.kind != 'spot' for b in books)):
        raise Invalid('TRIANGLE_ROUTE_INVALID')
    if key == 'triangle' and len({b.venue for b in books}) != 1:
        raise Invalid('CROSS_VENUE_CYCLE_REQUIRES_INVENTORY')
    if key == 'dex_atomic':
        if any(b.kind != 'dex' for b in books) or len({b.raw['chain'] for b in books}) != 1:
            raise Invalid('ATOMIC_CHAIN_INVALID')
        flag(s, 'simulation_verified')
        fresh(s['simulation_ts'], now, p['max_age'])
        flag(s, 'loan_cost_verified')
    if key == 'stable_regional':
        if s.get('subtype') != 'stable_cycle':
            raise Invalid('REGIONAL_OBSERVE_ONLY')
        flag(s, 'settlement_path_verified')
        if any(b.kind not in ('spot', 'dex') for b in books):
            raise Invalid('STABLE_ROUTE_INVALID')
        if len({b.venue for b in books}) != 1:
            raise Invalid('CROSS_VENUE_CYCLE_REQUIRES_INVENTORY')
    amount = positive(s['start_amount'])
    if amount > p['max_notional']:
        raise Invalid('NOTIONAL_LIMIT')
    initial, asset, steps, dust = amount, s['start_asset'], [], {}
    for b, side in zip(books, s['sides'], strict=True):
        before = amount
        if side == 'buy' and b.quote == asset:
            fill = b.buy_for(amount)
            residual = amount - fill.cash - fill.fee
            amount, new_asset = fill.quantity, b.base
        elif side == 'sell' and b.base == asset:
            q = common_quantity(amount, [b.step])
            fill = b.fill(q, 'sell')
            residual = amount - q
            amount, new_asset = fill.cash - fill.fee, b.quote
        else:
            raise Invalid('CYCLE_ASSET_OR_SIDE_MISMATCH')
        if residual < 0:
            raise Invalid('CYCLE_OVERSPEND')
        dust[asset] = dust.get(asset, ZERO) + residual
        steps.append(dict(instrument=b.id, venue=b.venue, side=side, asset_in=asset,
                          asset_out=new_asset, amount_in=before, amount_out=amount,
                          quantity=fill.quantity, cash=fill.cash, fee=fill.fee,
                          residual=residual))
        asset = new_asset
    if asset != s['start_asset']:
        raise Invalid('CYCLE_NOT_CLOSED')
    amount += dust.pop(asset, ZERO)
    gas = sum((D(b.raw['gas_entry']) for b in books if b.kind == 'dex'), ZERO)
    extra = nonnegative(s['extra_cost']) + gas
    loan_fee = nonnegative(s['loan_fee']) if key == 'dex_atomic' else ZERO
    if key == 'dex_atomic':
        minimum = positive(s['atomic_min_output'])
        if not initial + loan_fee + extra + p['min_net'] + p['safety'] <= minimum <= amount:
            raise Invalid('ATOMIC_MIN_PROFIT_INVALID')
    net = amount - initial - extra - loan_fee
    if net < p['min_net'] + p['safety']:
        raise Invalid('SCENARIO_NET_LOW')
    return dict(strategy=key, route=s['route'], ts=now, currency=p['currency'],
                kind='cycle', quantity=initial, legs=[], steps=steps, dust=dust,
                capital=initial + extra + loan_fee + p['safety'],
                claims=[dict(venue=books[0].venue, asset=s['start_asset'],
                             amount=initial+extra+loan_fee+p['safety'], inventory=False)],
                scenario_net=net, target_net=net-p['safety'], safety=p['safety'],
                entry_extra=extra+loan_fee, exit_extra=ZERO, stop_loss=p['stop_loss'],
                max_hold=p['max_hold'], evidence=s, quality='INDICATIVE_PAPER_REPLAY')


def evaluate(snapshot, values=None, now=None):
    """Return a serializable decision; malformed/unknown economics never opens."""
    import time
    try:
        with localcontext() as ctx:
            ctx.prec = 96
            s, p = snapshot, policy(values)
            stamp = D(time.time() if now is None else now)
            key = s['strategy']
            if key not in CATALOG:
                raise Invalid('STRATEGY_UNKNOWN')
            if s.get('environment') != 'paper' or s.get('source_quality') != 'replay':
                raise Invalid('PAPER_REPLAY_EVIDENCE_REQUIRED')
            if not isinstance(s.get('route'), str) or not 1 <= len(s['route']) <= 150:
                raise Invalid('ROUTE_INVALID')
            fresh(s['ts'], stamp, p['max_age'])
            if key == 'spot_transfer':
                raise Invalid('TRANSFER_OBSERVE_ONLY')
            if key == 'stable_regional' and s.get('subtype') == 'regional':
                raise Invalid('REGIONAL_OBSERVE_ONLY')
            plan = (_cycle if CATALOG[key].family == 'cycle' else _paired)(s, p, stamp)
            return dict(allowed=True, reason='PAPER_SCENARIO_ADMITTED', plan=plain(plan))
    except (Invalid, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        reason = str(exc) if isinstance(exc, Invalid) else 'EVIDENCE_INVALID:' + type(exc).__name__
        return dict(allowed=False, reason=reason, plan=None)
