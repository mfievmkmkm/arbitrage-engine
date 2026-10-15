"""Synthetic educational fixtures, never market evidence or profit estimates."""
import copy
from .strategy_catalog import CATALOG


def book(name='a', kind='perp', price=100, ts=1000, base='COIN', quote='USDT', **extra):
    row = dict(id=name, venue=name, base=base, quote=quote, asset_id='asset:'+base,
               quote_asset_id='asset:'+quote, kind=kind, ts=ts, identity_verified=True,
               linear=True, settle=quote, step='0.001', multiplier='1', min_qty='0.001',
               min_notional='0', fee_rate='0.0005', fee_currency='quote',
               bids=[[str(price), '1000']], asks=[[str(price), '1000']])
    if kind in ('future', 'call', 'put'):
        row.update(expiry=2000, settlement_verified=True, settlement_method='cash')
    if kind in ('call', 'put'):
        row.update(strike='100', option_style='european', margin_verified=True, margin_per_base='100')
    if kind == 'dex':
        row.update(chain='chain-a', contract='contract:'+base, token_properties_verified=True,
                   gas_verified=True, gas_entry='0.01', gas_exit='0.01',
                   transfer_tax_rate='0', rebasing=False,dex_quote_verified=True,
                   dex_quote_buy_qty='.9',dex_quote_sell_qty='.9')
    row.update(extra)
    return row


def snapshot(key='perp_convergence', ts=1000):
    s = dict(strategy=key, route='synthetic-'+key, ts=ts, environment='paper',
             source_quality='replay', quantity='0.9', horizon_seconds=1200,
             costs_verified=True, financing_cost='0', entry_extra_cost='0', exit_extra_cost='0',
             financing_accrued='0', funding_calendar_verified=True,
             funding_coverage_until={'a':ts+86400, 'b':ts+86400}, funding_events=[],
             funding_settlement_verified=True, funding_settled_until={'a':ts,'b':ts},
             rebalance=dict(verified=True, cost='0.10', deadline=ts+1200),
             books=[book('a', price=100, ts=ts), book('b', price=102, ts=ts)])
    kinds = {
        'spot_inventory': ('spot','spot'), 'spot_transfer':('spot','spot'),
        'spot_perp': ('spot','perp'), 'reverse_carry':('spot','perp'),
        'dated_carry':('spot','future'), 'calendar':('future','future'),
        'cex_dex_spot':('dex','spot'), 'dex_perp':('dex','perp'),
        'cross_chain':('dex','dex'),
    }.get(key)
    if kinds:
        s['books'] = [book('a', kinds[0], 100, ts), book('b', kinds[1], 102, ts)]
    if key == 'reverse_carry':
        s['books'][0] = book('a','spot',102,ts)
        s['books'][1] = book('b','perp',100,ts)
        s['borrow'] = dict(verified=True, instrument='a', quantity='1',
                           daily_rate='0.0001', available_until=ts+86400, recalled=False)
        s.update(borrow_verified=True, borrow_repaid=False, borrow_accrued='0', accounting_asof=ts)
    if key == 'perp_funding':
        s.update(adverse_basis_cost='0.1', funding_events=[dict(instrument='b', ts=ts+600,
                 rate='0.02', notional_price='102')])
    if key == 'dated_carry':
        s['spot_index_equivalence_verified'] = True
        s['books'][1]['expiry'] = ts+1000
    if key == 'calendar':
        s.update(curve_model_verified=True, target_spread='0.5')
        s['books'][0]['expiry'], s['books'][1]['expiry'] = ts+2000, ts+5000
    if key == 'cross_chain':
        s['books'][1]['chain'] = 'chain-b'
        s['cross_chain_equivalence_verified'] = True
    if key == 'options_parity':
        s.update(spot_index_equivalence_verified=True,
                 books=[book('a','spot',100,ts), book('p','put',2,ts), book('c','call',5,ts)])
        for b in s['books'][1:]:
            b['expiry'] = ts+1000
    if key in ('triangle', 'dex_atomic', 'stable_regional'):
        kind = 'dex' if key == 'dex_atomic' else 'spot'
        s.update(start_asset='USDT', start_amount='90', sides=['buy','sell','sell'],
                 extra_cost='0', loan_fee='0', simulation_verified=True, simulation_ts=ts,
                 loan_cost_verified=True, atomic_min_output='92',
                 subtype='stable_cycle', settlement_path_verified=True,
                 books=[book('ab',kind,10,ts,base='A',quote='USDT',venue='venue'),
                        book('ac',kind,2,ts,base='A',quote='B',venue='venue'),
                        book('bc',kind,5.2,ts,base='B',quote='USDT',venue='venue')])
        if kind=='dex':
            for b,q in zip(s['books'],('8.995','8.995','17.981')):
                b.update(dex_quote_buy_qty=q,dex_quote_sell_qty=q)
    return s


def all_snapshots(ts=1000):
    return [copy.deepcopy(snapshot(k,ts)) for k in CATALOG]
