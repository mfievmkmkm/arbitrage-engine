"""Durable isolated Paper/replay portfolio. Never imports an execution client.

The account is a valuation budget, not a simulated exchange wallet. Inventory
claims are explicitly seeded and stay reserved until a verified rebalance.
Reference fills assume displayed depth is available, not live fill probability.
"""
import asyncio
import copy
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from decimal import localcontext
import aiosqlite
from .strategy_catalog import CATALOG
from .strategy_math import D, ZERO, ONE, Book, Invalid, leg, plain, positive, nonnegative, price_pnl, flag, exact
from .strategy_models import evaluate, policy

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_lab_state(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategy_lab_events(id TEXT PRIMARY KEY,ts REAL NOT NULL,event TEXT NOT NULL,strategy TEXT,route TEXT,position_id TEXT,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strategy_lab_positions(id TEXT PRIMARY KEY,strategy TEXT NOT NULL,route TEXT NOT NULL,status TEXT NOT NULL,net TEXT,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS strategy_lab_event_ts ON strategy_lab_events(ts);
"""


def day(ts):
    # Same operator day as the Russian project (UTC+5), independent of host TZ.
    return datetime.fromtimestamp(float(D(ts)+D(18000)), timezone.utc).date().isoformat()


@exact
def seed_inventory(items, capital):
    result, value = {}, ZERO
    for item in items:
        key = item['venue'] + '|' + item['asset_id']
        if key in result or not item['venue'] or not item['asset_id']:
            raise Invalid('INVENTORY_SEED_DUPLICATE')
        qty, price = positive(item['quantity']), positive(item['price'])
        value += qty * price
        result[key] = str(qty)
    if value > capital:
        raise Invalid('INVENTORY_SEED_EXCEEDS_CAPITAL')
    return result, {item['venue']+'|'+item['asset_id']:str(positive(item['price'])) for item in items}


class Lab:
    def __init__(self, path, capital='250', clock=time.time):
        self.path, self.initial, self.clock = str(path), positive(capital), clock
        self.lock = asyncio.Lock()
        self.state, self.version = None, 0
        self.tickets = {}
        self.on_open = None

    async def init(self):
        initial = dict(capital=str(self.initial), realized='0', positions={}, latest={},
                       inventory={}, inventory_prices={}, last_entry={}, settings=dict(mode='observe', enabled=[], revision=0,
                       policy=plain(policy()), max_positions=1, daily_loss='5'),
                       daily={}, closed_count=0, last_ts='0',timeline=None,source_errors={})
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.execute('INSERT OR IGNORE INTO strategy_lab_state VALUES(1,0,?)', (json.dumps(initial),))
            async with db.execute('SELECT version,payload FROM strategy_lab_state WHERE id=1') as cur:
                version, payload = await cur.fetchone()
            await db.commit()
        self.version, self.state = version, json.loads(payload)
        if D(self.state['capital']) != self.initial:
            raise Invalid('PERSISTED_LAB_CAPITAL_CONFLICT')

    @property
    def positions(self):
        return self.state['positions'] if self.state else {}

    @property
    @exact
    def reserved(self):
        return sum((D(x['plan']['capital']) for x in self.positions.values()), ZERO)

    @property
    @exact
    def equity(self):
        return D(self.state['capital']) + D(self.state['realized'])

    async def _commit(self, state, event_id, ts, event, payload, saved=()):
        payload = plain(payload)
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT 1 FROM strategy_lab_events WHERE id=?', (event_id,)) as cur:
                if await cur.fetchone():
                    return False
            cursor = await db.execute('UPDATE strategy_lab_state SET version=version+1,payload=? WHERE id=1 AND version=?',
                                     (json.dumps(plain(state), ensure_ascii=False), self.version))
            if cursor.rowcount != 1:
                raise Invalid('LAB_CONCURRENT_WRITER_RELOAD_REQUIRED')
            await db.execute('INSERT INTO strategy_lab_events VALUES(?,?,?,?,?,?,?)',
                             (event_id, float(ts), event, payload.get('strategy'), payload.get('route'),
                              payload.get('position_id'), json.dumps(payload, ensure_ascii=False)))
            for pos in saved:
                await db.execute('INSERT INTO strategy_lab_positions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,net=excluded.net,payload=excluded.payload',
                                 (pos['id'], pos['plan']['strategy'], pos['plan']['route'], pos['status'],
                                  pos.get('net'), json.dumps(plain(pos), ensure_ascii=False)))
            await db.commit()
        self.state, self.version = plain(state), self.version + 1
        return True

    async def configure(self, mode=None, enabled=None, values=None, max_positions=None, daily_loss=None, inventory=None):
        async with self.lock:
            state = copy.deepcopy(self.state)
            settings = state['settings']
            if mode is not None:
                if mode not in ('observe', 'manual', 'auto'):
                    raise Invalid('MODE_INVALID')
                settings['mode'] = mode
            if enabled is not None:
                if not isinstance(enabled, list) or not set(enabled) <= set(CATALOG):
                    raise Invalid('STRATEGY_UNKNOWN')
                settings['enabled'] = sorted(set(enabled))
            if values is not None:
                settings['policy'] = plain(policy(dict(settings['policy'], **values)))
            if max_positions is not None:
                if isinstance(max_positions, bool) or int(max_positions) != max_positions or not 1 <= max_positions <= 5:
                    raise Invalid('POSITION_LIMIT_INVALID')
                settings['max_positions'] = max_positions
            if daily_loss is not None:
                settings['daily_loss'] = str(positive(daily_loss))
            if inventory is not None:
                if state['positions'] or state['closed_count']:
                    raise Invalid('INVENTORY_SEED_ACCOUNT_ALREADY_USED')
                state['inventory'], state['inventory_prices'] = seed_inventory(inventory, D(state['capital']))
            settings['revision'] += 1
            await self._commit(state, secrets.token_hex(16), self.clock(), 'CONFIG_CHANGE', settings)
            self.tickets.clear()

    async def source_error(self,key,error,ts):
        async with self.lock:
            state=copy.deepcopy(self.state)
            state.setdefault('source_errors',{})[key]=str(error)[:200]
            saved=[]
            for pos in state['positions'].values():
                if self.route_key(pos['plan'])==key:
                    pos['data_error']=str(error)[:200]
                    saved.append(pos)
            await self._commit(state,secrets.token_hex(16),ts,'SOURCE_UNAVAILABLE',
                               dict(route=key,reason=str(error)[:200]),saved)

    def issue(self, route_key):
        settings = self.state['settings']
        item = self.state['latest'].get(route_key)
        if settings['mode'] != 'manual' or not item or not item['decision']['allowed']:
            raise Invalid('MANUAL_CARD_UNAVAILABLE')
        now = D(self.clock())
        if not ZERO <= now-D(item['snapshot']['ts']) <= D(settings['policy']['max_age']):
            raise Invalid('MANUAL_CARD_STALE')
        token = secrets.token_hex(12)
        self.tickets = {k:v for k,v in self.tickets.items() if now-D(v['issued']) <= 30}
        self.tickets[token] = dict(key=route_key, issued=str(now), revision=settings['revision'])
        return token

    async def confirm(self, token, refresh):
        ticket = self.tickets.pop(token, None)
        now = D(self.clock())
        if (not ticket or not ZERO <= now-D(ticket['issued']) <= 30
                or ticket['revision'] != self.state['settings']['revision']
                or self.state['settings']['mode'] != 'manual'):
            raise Invalid('MANUAL_CARD_EXPIRED')
        # A real refresh provider is required. Replaying the preview is insufficient.
        snapshot = await refresh(ticket['key'])
        if not snapshot or self.route_key(snapshot) != ticket['key']:
            raise Invalid('MANUAL_REFRESH_UNAVAILABLE')
        now=D(self.clock())
        if not ZERO<=now-D(ticket['issued'])<=30:
            raise Invalid('MANUAL_CARD_EXPIRED')
        return await self.process(dict(id=secrets.token_hex(16), type='snapshot', snapshot=snapshot),
                                  now=now, manual_revision=ticket['revision'])

    @staticmethod
    def route_key(s):
        return str(s['strategy']) + ':' + str(s['route'])

    def _entry_reason(self, state, plan, now, manual_revision):
        settings = state['settings']
        if settings['mode'] == 'observe':
            return 'LAB_OBSERVE_ONLY'
        if settings['mode'] == 'manual' and manual_revision != settings['revision']:
            return 'LAB_MANUAL_CONFIRM_REQUIRED'
        if plan['strategy'] not in settings['enabled']:
            return 'LAB_STRATEGY_DISABLED'
        route = self.route_key(plan)
        if D(plan['evidence']['ts']) <= D(state.get('last_entry', {}).get(route, '-1')):
            return 'LAB_ENTRY_SNAPSHOT_ALREADY_USED'
        if any(x['status'] in ('ENTRY_UNCERTAIN', 'RECOVERY_REQUIRED', 'CYCLE_EXECUTING', 'CYCLE_RECOVERY', 'ATOMIC_PENDING') for x in state['positions'].values()):
            return 'LAB_UNHEDGED_POSITION'
        if len(state['positions']) >= settings['max_positions']:
            return 'LAB_POSITION_LIMIT'
        if D(state['daily'].get(day(now), '0')) <= -D(settings['daily_loss']):
            return 'LAB_DAILY_STOP'
        reserved = sum((D(x['plan']['capital']) for x in state['positions'].values()), ZERO)
        inventory_claims = {}
        for pos in list(state['positions'].values()) + [dict(plan=plan)]:
            for claim in pos['plan']['claims']:
                if claim['inventory']:
                    key = claim['venue'] + '|' + claim['asset']
                    inventory_claims[key] = inventory_claims.get(key, ZERO) + D(claim['amount'])
        if any(q > D(state['inventory'].get(k, '0')) for k, q in inventory_claims.items()):
            return 'LAB_INVENTORY_LOW'
        unused_inventory = sum(((D(q)-inventory_claims.get(k,ZERO))*D(state.get('inventory_prices',{}).get(k,0))
                                for k,q in state['inventory'].items()),ZERO)
        if reserved + D(plan['capital']) + unused_inventory > D(state['capital']) + D(state['realized']):
            return 'LAB_CAPITAL_LOW'
        return None

    @staticmethod
    def _realize(state, pos, net, now, reason):
        pos.update(status='CLOSED', net=str(net), closed_at=str(now), exit_reason=reason)
        state['realized'] = str(D(state['realized']) + net)
        state['daily'][day(now)] = str(D(state['daily'].get(day(now), '0')) + net)
        state['closed_count'] += 1
        state['positions'].pop(pos['id'], None)

    @staticmethod
    def _entry_fills(pos, fills):
        plan = pos['plan']
        if not isinstance(fills, list) or {x['instrument'] for x in fills} != {x['instrument'] for x in plan['legs']} or len(fills) != len(plan['legs']):
            raise Invalid('ENTRY_FILL_SET_INVALID')
        by_id = {x['instrument']: x for x in fills}
        previous = {x['instrument']:x for x in pos.get('entry_fills', []) if x['status']=='final'}
        held, unknown = [], False
        for planned in plan['legs']:
            f = by_id[planned['instrument']]
            if planned['instrument'] in previous and f != previous[planned['instrument']]:
                raise Invalid('CONFIRMED_FILL_IMMUTABLE')
            if f['status'] == 'unknown':
                unknown = True
                continue
            if f['status'] != 'final':
                raise Invalid('ENTRY_FILL_STATUS_INVALID')
            q = nonnegative(f['quantity'])
            if q > D(planned['quantity']) or q % D(planned['step']):
                raise Invalid('ENTRY_FILL_QUANTITY_INVALID')
            fee = nonnegative(f['fee'])
            if q == 0:
                if fee != 0:
                    raise Invalid('EMPTY_FILL_WITH_FEE')
                continue
            price = positive(f['price'])
            held.append(dict(planned, quantity=str(q), entry_price=str(price),
                             entry_cash=str(q*price), entry_fee=str(fee)))
        pos['held_legs'] = held
        pos['entry_fills'] = fills
        pos['status'] = ('ENTRY_UNCERTAIN' if unknown else 'HEDGED' if len(held) == len(plan['legs'])
                         and len({D(x['quantity']) for x in held}) == 1
                         and D(held[0]['quantity']) == D(plan['quantity']) else 'RECOVERY_REQUIRED')

    @staticmethod
    def _accounting(pos, snapshot, until):
        perps = {x['instrument'] for x in pos['held_legs'] if x['kind'] == 'perp'}
        coverage = snapshot.get('funding_settled_until', {})
        known = not perps or (snapshot.get('funding_settlement_verified') is True
                             and all(D(coverage.get(k, 0)) >= until for k in perps))
        expected = {(x['instrument'], D(x['ts'])) for x in pos['plan']['funding_scenario']
                    if x['instrument'] in perps and D(x['ts']) <= until}
        received = {(x['instrument'], D(x['ts'])) for x in pos['cashflows']}
        known = known and expected <= received
        # Only ledger events are income, never the admission forecast.
        funding = sum((D(x['cashflow']) for x in pos['cashflows']
                       if D(x['ts']) <= until), ZERO)
        financing = nonnegative(snapshot.get('financing_accrued', 0))
        borrow = nonnegative(snapshot.get('borrow_accrued', 0))
        if D(pos['plan']['financing_reserve']) > 0 and (snapshot.get('financing_verified') is not True or 'financing_accrued' not in snapshot):
            known = False
        if pos['plan']['strategy'] == 'reverse_carry':
            known = (known and snapshot.get('borrow_verified') is True
                     and 'borrow_accrued' in snapshot)
        if D(pos['plan']['financing_reserve']) > 0 or pos['plan']['strategy']=='reverse_carry':
            known = known and D(snapshot.get('accounting_asof', -1)) == until
        return known, funding, financing, borrow

    def _mark(self, state, pos, s, now):
        if pos['status'] == 'ATOMIC_PENDING':
            return 'ATOMIC_RECEIPT_REQUIRED'
        if pos['status'] == 'ENTRY_UNCERTAIN':
            return 'ENTRY_RECONCILIATION_REQUIRED'
        if pos['status'] == 'REBALANCE_PENDING':
            return 'REBALANCE_REQUIRED'
        if pos['status'] in ('CYCLE_EXECUTING', 'CYCLE_RECOVERY'):
            if pos.get('close_requested') or now-D(pos['opened_at']) >= D(pos['plan']['max_hold']):
                pos['status'] = 'CYCLE_RECOVERY'
                return 'CYCLE_UNWIND_QUOTE_REQUIRED'
            expected = pos['plan']['steps'][pos['cycle_index']]['instrument']
            row = next((x for x in s['books'] if x['id']==expected),None)
            if row is None:
                raise Invalid('CYCLE_NEXT_BOOK_MISSING')
            return self._cycle_step(state,pos,row,now)
        if pos.get('exit_frozen'):
            frozen = pos['exit_frozen']
            known, funding, financing, borrow = self._accounting(pos, s, D(frozen['ts']))
            if known and (pos['plan']['strategy']!='reverse_carry' or s.get('borrow_repaid') is True):
                net = D(frozen['price_net']) + funding - financing - borrow
                self._realize(state, pos, net, now, frozen['reason'])
                return 'EXIT_ACCOUNTING_COMPLETE'
            return 'EXIT_ACCOUNTING_PENDING'
        p = pos['plan']
        books = {x['id']: Book(x, now, state['settings']['policy']['max_age']) for x in s['books']}
        exits = []
        for held in pos['held_legs']:
            b = books[held['instrument']]
            if (b.asset != held['asset_id'] or b.venue != held['venue'] or b.kind != held['kind']
                    or b.quote != held['quote'] or b.raw['quote_asset_id'] != held['quote_asset_id']
                    or b.step != D(held['step']) or D(b.raw['multiplier']) != D(held['multiplier'])
                    or b.raw.get('expiry') != held['expiry'] or b.raw.get('strike') != held['strike']
                    or b.raw.get('option_style') != held['option_style']
                    or b.raw.get('chain') != held['chain'] or b.raw.get('contract') != held['contract']):
                raise Invalid('EXIT_INSTRUMENT_CHANGED')
            if (held['expiry'] is not None and D(held['expiry']) <= now):
                pos['status']='RECOVERY_REQUIRED' if p['strategy']=='calendar' else 'SETTLEMENT_PENDING'
                raise Invalid('EXPIRED_USE_SETTLEMENT_EVENT')
            exits.append(leg(b, held['side'], D(held['quantity'])))
        flag(s, 'costs_verified')
        extra = nonnegative(s['exit_extra_cost']) + sum(
            (D(books[x['instrument']].raw['gas_exit']) for x in pos['held_legs'] if x['kind'] == 'dex'), ZERO)
        gross = price_pnl(pos['held_legs'], exits)
        fees = sum((D(x['entry_fee']) for x in pos['held_legs']), ZERO) + sum((D(x['exit_fee']) for x in exits), ZERO)
        price_net = gross - fees - D(p['entry_extra']) - extra
        known, funding, financing, borrow = self._accounting(pos, s, now)
        net = price_net + funding - financing - borrow
        pos['last_mark'] = plain(dict(ts=now, price_pnl=gross, entry_exit_fees=fees,
                                      price_net=price_net, funding=funding, funding_known=known,
                                      net=net if known else None, exits=exits))
        if known:
            pos['best_net'] = str(max(D(pos['best_net']), net))
        elapsed = now-D(pos['opened_at'])
        # Positive funding never erases an already breached conservative price stop.
        risk_net = min(price_net, net) if known else price_net
        reason = None
        if pos['status'] == 'RECOVERY_REQUIRED':
            reason = 'UNHEDGED_RECOVERY'
        elif pos.get('close_requested'):
            reason = 'MANUAL_EXIT'
        elif risk_net <= -D(p['stop_loss']):
            reason = 'NET_STOP'
        elif elapsed >= D(p['max_hold']):
            reason = 'TIME_LIMIT'
        elif s.get('risk_exit') is True:
            reason = 'SOURCE_RISK_EXIT'
        elif p['strategy'] in ('perp_funding', 'spot_perp', 'reverse_carry') and s.get('carry_invalidated') is True:
            reason = 'CARRY_INVALIDATED'
        elif known and net >= D(p['target_net']):
            reason = 'NET_TARGET'
        elif (known and D(pos['best_net']) >= D(p['trail_arm'])
              and net <= D(pos['best_net'])*(ONE-D(p['trail_giveback']))):
            reason = 'PROFIT_GIVEBACK'
        if reason:
            pos['exit_frozen'] = plain(dict(ts=now, price_net=price_net, reason=reason, exits=exits))
            if known and (p['strategy']!='reverse_carry' or s.get('borrow_repaid') is True):
                self._realize(state, pos, net, now, reason)
                return reason
            pos['status'] = 'EXIT_ACCOUNTING_PENDING'
            return 'EXIT_ACCOUNTING_PENDING'
        return 'MARKED' if known else 'FUNDING_ACCOUNTING_UNKNOWN'

    def _cycle_step(self, state, pos, row, now, unwind=False, side=None):
        plan = pos['plan']
        b = Book(row,now,state['settings']['policy']['max_age'])
        asset, amount = pos['cycle_asset'],D(pos['cycle_amount'])
        if not unwind:
            expected = plan['steps'][pos['cycle_index']]
            side = expected['side']
            if b.id!=expected['instrument'] or b.venue!=expected['venue']:
                raise Invalid('CYCLE_STEP_INSTRUMENT_MISMATCH')
        identities={}
        for original in plan['evidence']['books']:
            identities[original['base']]=original['asset_id']
            identities[original['quote']]=original['quote_asset_id']
        if identities.get(b.base)!=b.asset or identities.get(b.quote)!=b.raw['quote_asset_id']:
            raise Invalid('CYCLE_STEP_TOKEN_MISMATCH')
        if b.venue!=plan['steps'][0]['venue']:
            raise Invalid('CYCLE_STEP_VENUE_MISMATCH')
        original = next((x for x in plan['evidence']['books'] if x['id']==b.id),None)
        if b.kind not in ('spot','dex') or (original and original['kind']!=b.kind):
            raise Invalid('CYCLE_STEP_KIND_INVALID')
        if side=='buy' and b.quote==asset:
            fill=b.buy_for(amount)
            residual=amount-fill.cash-fill.fee
            amount,new_asset=fill.quantity,b.base
        elif side=='sell' and b.base==asset:
            from .strategy_math import common_quantity
            q=common_quantity(amount,[b.step])
            fill=b.fill(q,'sell')
            residual=amount-q
            amount,new_asset=fill.cash-fill.fee,b.quote
        else:
            raise Invalid('CYCLE_STEP_ASSET_MISMATCH')
        if residual<0:
            raise Invalid('CYCLE_OVERSPEND')
        if unwind and new_asset!=plan['currency']:
            raise Invalid('UNWIND_MUST_RETURN_TO_START_ASSET')
        pos['cycle_dust'][asset]=str(D(pos['cycle_dust'].get(asset,0))+residual)
        pos['cycle_fills'].append(plain(dict(ts=now,instrument=b.id,side=side,quantity=fill.quantity,
                                            cash=fill.cash,fee=fill.fee,asset_in=asset,asset_out=new_asset)))
        pos['cycle_asset'],pos['cycle_amount']=new_asset,str(amount)
        pos['cycle_index']+=1
        pos['cycle_cost']=str(D(pos.get('cycle_cost',0))+(D(b.raw['gas_entry']) if b.kind=='dex' else ZERO))
        if unwind or pos['cycle_index']==len(plan['steps']):
            if new_asset!=plan['currency']:
                raise Invalid('CYCLE_FINAL_ASSET_MISMATCH')
            net=amount+D(pos['cycle_dust'].pop(new_asset,'0'))-D(plan['quantity'])-D(pos['cycle_cost'])
            self._realize(state,pos,net,now,'CYCLE_UNWIND' if unwind else 'SEQUENTIAL_CYCLE_COMPLETE')
            return 'CYCLE_UNWIND' if unwind else 'SEQUENTIAL_CYCLE_COMPLETE'
        pos['status']='CYCLE_EXECUTING'
        return 'CYCLE_STEP_FILLED'

    async def process(self, event, now=None, manual_revision=None, allow_entries=True):
        async with self.lock:
            with localcontext() as ctx:
                ctx.prec = 96
                if not isinstance(event,dict):
                    raise Invalid('EVENT_OBJECT_REQUIRED')
                event=plain(event)
                stamp = D(self.clock() if now is None else now)
                if not ZERO<=stamp<=D('253402200000'):
                    raise Invalid('EVENT_TIME_INVALID')
                event_id = event.get('id')
                if not isinstance(event_id, str) or not 1 <= len(event_id) <= 150:
                    raise Invalid('EVENT_ID_REQUIRED')
                async with aiosqlite.connect(self.path) as db:
                    async with db.execute('SELECT payload FROM strategy_lab_events WHERE id=?', (event_id,)) as cur:
                        previous = await cur.fetchone()
                if previous:
                    original = json.loads(previous[0])
                    if original.get('input') != event:
                        raise Invalid('EVENT_ID_REUSED_DIFFERENT_PAYLOAD')
                    return dict(reason='DUPLICATE_EVENT', original=original)
                if stamp < D(self.state['last_ts']):
                    raise Invalid('EVENT_TIME_REVERSED')
                state = copy.deepcopy(self.state)
                if manual_revision is not None and (state['settings']['revision']!=manual_revision
                        or state['settings']['mode']!='manual'):
                    raise Invalid('MANUAL_CARD_EXPIRED')
                state['last_ts'] = str(stamp)
                saved, out = [], dict(reason='EVENT_UNSUPPORTED')
                kind = event['type']
                if kind == 'snapshot':
                    s = event['snapshot']
                    timeline='public_capture' if s.get('source_origin')=='public_capture' else 'replay'
                    if state.get('timeline') not in (None,timeline):
                        raise Invalid('SEPARATE_REPLAY_AND_PUBLIC_CAPTURE_ACCOUNTS_REQUIRED')
                    state['timeline']=timeline
                    key = self.route_key(s)
                    state.setdefault('source_errors',{}).pop(key,None)
                    decision = evaluate(s, state['settings']['policy'], stamp)
                    out = dict(strategy=s['strategy'], route=s['route'], decision=decision)
                    matching = [x for x in state['positions'].values()
                                if self.route_key(x['plan']) == key]
                    if matching:
                        pos = matching[0]
                        try:
                            if (s.get('environment') != 'paper' or s.get('source_quality') != 'replay'
                                    or not ZERO <= stamp-D(s['ts']) <= D(state['settings']['policy']['max_age'])):
                                raise Invalid('MONITOR_EVIDENCE_INVALID')
                            out['reason'] = self._mark(state, pos, s, stamp)
                            pos.pop('data_error', None)
                        except (Invalid, KeyError, TypeError, ValueError, ArithmeticError) as exc:
                            pos['data_error'] = str(exc)
                            out['reason'] = 'MONITOR_UNAVAILABLE'
                        out['position_id'] = pos['id']
                        saved.append(pos)
                    elif decision['allowed']:
                        plan = decision['plan']
                        # Seed inventory is already part of the valuation budget;
                        # a lower entry mark must not free its original reserve.
                        inventory_basis_extra=ZERO
                        for x in plan['legs']:
                            if x['side']=='sell' and x['kind'] in ('spot','dex') and plan['kind']=='inventory':
                                basis=D(state.get('inventory_prices',{}).get(x['venue']+'|'+x['asset_id'],0))
                                inventory_basis_extra+=max(ZERO,D(x['quantity'])*basis-D(x['entry_cash']))
                        plan['capital']=str(D(plan['capital'])+inventory_basis_extra)
                        reason = self._entry_reason(state, plan, stamp, manual_revision) if allow_entries else 'LAB_OPERATOR_GATE'
                        out['reason'] = reason or 'PAPER_OPEN'
                        if not reason:
                            state.setdefault('last_entry', {})[key] = str(s['ts'])
                            pid = hashlib.sha256(event_id.encode()).hexdigest()[:24]
                            pos = dict(id=pid, plan=plan, opened_at=str(stamp), status='HEDGED',
                                       best_net='0', cashflows=[], net=None, held_legs=plan['legs'])
                            if plan['kind'] == 'cycle':
                                if s['strategy'] == 'dex_atomic':
                                    # A simulation/min-output only admits a route.
                                    # It cannot credit a transaction's forecast as PNL.
                                    pos['status'] = 'ATOMIC_PENDING'
                                    state['positions'][pid] = pos
                                else:
                                    pos.update(status='CYCLE_EXECUTING',cycle_index=0,
                                               cycle_asset=s['start_asset'],cycle_amount=s['start_amount'],
                                               cycle_fills=[],cycle_dust={},cycle_cost=str(nonnegative(s['extra_cost'])))
                                    state['positions'][pid]=pos
                                    self._cycle_step(state,pos,s['books'][0],stamp)
                            elif plan['kind'] == 'inventory':
                                pos['status'] = 'REBALANCE_PENDING'
                                pos['trade_net'] = str(sum((D(x['entry_cash'])*(ONE if x['side']=='sell' else -ONE)-D(x['entry_fee']) for x in plan['legs']), ZERO)-D(plan['entry_extra']))
                                state['positions'][pid] = pos
                            else:
                                if 'entry_fills' in s:
                                    self._entry_fills(pos, s['entry_fills'])
                                state['positions'][pid] = pos
                                # Initial executable exit is a cost, never default profit zero.
                                if pos['status'] != 'ENTRY_UNCERTAIN':
                                    self._mark(state, pos, s, stamp)
                            saved.append(pos)
                            out['position_id'] = pid
                    else:
                        out['reason'] = decision['reason']
                    state['latest'].pop(key, None)
                    state['latest'][key] = dict(snapshot=s, decision=decision, reason=out['reason'])
                    while len(state['latest']) > 40:
                        state['latest'].pop(next(iter(state['latest'])))
                else:
                    pid = event['position_id']
                    if pid not in state['positions']:
                        raise Invalid('POSITION_NOT_OPEN')
                    pos = state['positions'][pid]
                    out = dict(strategy=pos['plan']['strategy'], route=pos['plan']['route'], position_id=pid)
                    if kind == 'funding':
                        ids = {x['instrument'] for x in pos['held_legs'] if x['kind']=='perp'}
                        end = D(pos.get('exit_frozen', {}).get('ts', stamp))
                        ts = D(event['settlement_ts'])
                        if event['instrument'] not in ids or not D(pos['opened_at']) < ts <= end:
                            raise Invalid('FUNDING_OUTSIDE_POSITION')
                        flag(event, 'verified')
                        identity = (event['instrument'], ts)
                        if any((x['instrument'], D(x['ts'])) == identity for x in pos['cashflows']):
                            raise Invalid('FUNDING_SETTLEMENT_DUPLICATE')
                        pos['cashflows'].append(dict(instrument=event['instrument'], ts=str(ts), cashflow=str(D(event['cashflow']))))
                        out['reason'] = 'FUNDING_RECORDED'
                    elif kind == 'reconcile_entry':
                        if pos['status'] != 'ENTRY_UNCERTAIN':
                            raise Invalid('RECONCILIATION_NOT_EXPECTED')
                        self._entry_fills(pos, event['fills'])
                        out['reason'] = pos['status']
                    elif kind == 'rebalance':
                        if pos['status'] != 'REBALANCE_PENDING':
                            raise Invalid('REBALANCE_NOT_EXPECTED')
                        flag(event, 'verified')
                        if D(event['quantity']) != D(pos['plan']['quantity']):
                            raise Invalid('REBALANCE_QUANTITY_MISMATCH')
                        cost = nonnegative(event['cost'])
                        self._realize(state, pos, D(pos['trade_net'])-cost, stamp, 'REBALANCE_CONFIRMED')
                        out['reason'] = 'REBALANCE_CONFIRMED'
                    elif kind == 'atomic_receipt':
                        if pos['status'] != 'ATOMIC_PENDING' or pos['plan']['strategy'] != 'dex_atomic':
                            raise Invalid('ATOMIC_RECEIPT_NOT_EXPECTED')
                        flag(event, 'verified')
                        evidence = pos['plan']['evidence']
                        if (event.get('chain') != evidence['books'][0]['chain']
                                or event.get('asset') != pos['plan']['currency']
                                or not isinstance(event.get('tx_id'), str) or not event['tx_id']):
                            raise Invalid('ATOMIC_RECEIPT_IDENTITY_MISMATCH')
                        gas = nonnegative(event['gas_paid'])
                        other = nonnegative(event['other_cost_paid'])
                        if event.get('success') is True:
                            if D(event['input_spent']) != D(pos['plan']['quantity']):
                                raise Invalid('ATOMIC_RECEIPT_INPUT_MISMATCH')
                            output = nonnegative(event['output_received'])
                            if output < D(evidence['atomic_min_output']):
                                raise Invalid('ATOMIC_RECEIPT_MIN_OUTPUT_MISMATCH')
                            loan = nonnegative(event['loan_fee_paid'])
                            net = output-D(pos['plan']['quantity'])-gas-other-loan
                            reason = 'ATOMIC_RECEIPT_COMPLETE'
                        elif event.get('success') is False:
                            net = -gas-other
                            reason = 'ATOMIC_RECEIPT_REVERT'
                        else:
                            raise Invalid('ATOMIC_RECEIPT_STATUS_INVALID')
                        pos['receipt'] = plain(event)
                        self._realize(state, pos, net, stamp, reason)
                        out['reason'] = reason
                    elif kind == 'request_close':
                        if pos['status'] not in ('HEDGED', 'RECOVERY_REQUIRED', 'CYCLE_EXECUTING', 'CYCLE_RECOVERY'):
                            raise Invalid('CLOSE_REQUEST_NOT_ACTIONABLE')
                        pos['close_requested'] = True
                        out['reason'] = 'CLOSE_REQUESTED_FRESH_QUOTES_REQUIRED'
                    elif kind in ('cycle_step','cycle_unwind'):
                        if pos['status'] not in ('CYCLE_EXECUTING','CYCLE_RECOVERY'):
                            raise Invalid('CYCLE_EXECUTION_NOT_EXPECTED')
                        out['reason']=self._cycle_step(state,pos,event['book'],stamp,
                                                       unwind=kind=='cycle_unwind',side=event.get('side'))
                    elif kind == 'settlement':
                        if pos['plan']['strategy'] not in ('dated_carry', 'options_parity','calendar','reverse_carry') or pos.get('exit_frozen'):
                            raise Invalid('SETTLEMENT_NOT_EXPECTED')
                        flag(event, 'verified')
                        if (pos['status'] not in ('HEDGED','SETTLEMENT_PENDING','RECOVERY_REQUIRED')
                                or len(pos['held_legs'])!=len(pos['plan']['legs'])
                                or any(x['kind']=='perp' for x in pos['held_legs'])):
                            raise Invalid('SETTLEMENT_REQUIRES_HEDGED_POSITION')
                        index = positive(event['index_price'])
                        books = {x['id']:Book(x, stamp, state['settings']['policy']['max_age']) for x in event['spot_books']}
                        derivative_books={x['id']:Book(x,stamp,state['settings']['policy']['max_age']) for x in event.get('derivative_books',[])}
                        gross, fees = ZERO, ZERO
                        for held in pos['held_legs']:
                            q = D(held['quantity'])
                            side = ONE if held['side']=='buy' else -ONE
                            fees += D(held['entry_fee'])
                            if held['kind']=='spot':
                                b = books[held['instrument']]
                                if b.asset != held['asset_id'] or b.venue != held['venue'] or b.quote != held['quote'] or b.kind!='spot':
                                    raise Invalid('SETTLEMENT_SPOT_MISMATCH')
                                fill = b.fill(q, 'sell' if held['side']=='buy' else 'buy')
                                gross += (fill.cash - D(held['entry_cash']))*side
                                fees += fill.fee
                            else:
                                if stamp<D(held['expiry']) and pos['plan']['strategy']=='calendar':
                                    b=derivative_books[held['instrument']]
                                    if (b.asset!=held['asset_id'] or b.quote!=held['quote'] or b.venue!=held['venue']
                                            or b.kind!='future' or D(b.raw['expiry'])!=D(held['expiry'])):
                                        raise Invalid('CALENDAR_REMAINDER_MISMATCH')
                                    fill=b.fill(q,'sell' if held['side']=='buy' else 'buy')
                                    gross+=(fill.cash-D(held['entry_cash']))*side
                                    fees+=fill.fee
                                    continue
                                expiry=event.get('settlement_expiries',{}).get(held['instrument'],event.get('expiry'))
                                if stamp < D(held['expiry']) or D(expiry) != D(held['expiry']):
                                    raise Invalid('SETTLEMENT_TIME_INVALID')
                                if pos['plan']['strategy']=='calendar' and held['instrument'] not in event.get('index_prices',{}):
                                    raise Invalid('CALENDAR_INDIVIDUAL_SETTLEMENT_INDEX_REQUIRED')
                                terminal = positive(event.get('index_prices',{}).get(held['instrument'],index))
                                if held['kind']=='put':
                                    terminal = max(ZERO, D(held['strike'])-index)
                                elif held['kind']=='call':
                                    terminal = max(ZERO, index-D(held['strike']))
                                gross += q*(terminal-D(held['entry_price']))*side
                                fees += nonnegative(event['settlement_fees'][held['instrument']])
                        financing = nonnegative(event['financing_cost'])
                        exit_cost = nonnegative(event['exit_extra_cost'])
                        borrow=ZERO
                        if pos['plan']['strategy']=='reverse_carry':
                            flag(event,'borrow_repaid')
                            flag(event,'borrow_verified')
                            borrow=nonnegative(event['borrow_cost'])
                        net = gross-fees-D(pos['plan']['entry_extra'])-financing-exit_cost-borrow
                        pos['settlement'] = plain(event)
                        self._realize(state, pos, net, stamp, 'EXPIRY_SETTLEMENT')
                        out['reason'] = 'EXPIRY_SETTLEMENT'
                    else:
                        raise Invalid('EVENT_UNSUPPORTED')
                    saved.append(pos)
                committed = await self._commit(state, event_id, stamp, kind, dict(out, input=event), saved)
                if committed and out.get('reason') == 'PAPER_OPEN' and self.on_open:
                    try:
                        await self.on_open(copy.deepcopy(pos))
                    except Exception:
                        import logging
                        logging.getLogger('arbitrage').exception('Strategy lab open notification failed')
                return plain(out) if committed else dict(reason='DUPLICATE_EVENT')

    async def export(self, limit=5000):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute('SELECT payload FROM strategy_lab_events ORDER BY rowid DESC LIMIT ?', (min(10000,max(1,int(limit))),)) as cur:
                rows = await cur.fetchall()
        return dict(environment='paper', account='isolated_strategy_lab', quality='INDICATIVE_PAPER_REPLAY',
                    state=self.state, events=[json.loads(x[0]) for x in reversed(rows)])
