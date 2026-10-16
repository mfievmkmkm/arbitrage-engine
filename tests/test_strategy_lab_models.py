import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import pytest
from app.strategy_math import D, Book, Invalid, common_quantity, price_pnl
from app.strategy_models import evaluate
from app.strategy_catalog import CATALOG
from app.strategy_lab_examples import snapshot, book


@pytest.mark.parametrize('key', list(CATALOG))
def test_every_playbook_mechanism_has_a_distinct_model(key):
    result = evaluate(snapshot(key),now=1000)
    if key=='spot_transfer':
        assert result['reason']=='TRANSFER_OBSERVE_ONLY'
    else:
        assert result['allowed'],result
        assert result['plan']['strategy']==key
        assert D(result['plan']['scenario_net'])>=D('.35')


@pytest.mark.parametrize('steps,requested,expected',[
    (['.003','.002'],'1','.996'), (['.01','.025'],'1.234','1.2'),
    (['.00000001','.00000003'],'.0000001','.00000009'),
    (['3','7'],'20','0'), (['.2','.3'],'2','1.8')])
def test_common_native_lattice(steps,requested,expected):
    assert common_quantity(requested,steps)==D(expected)


def test_vwap_and_fee_are_not_mid_or_double_slippage():
    b=book(kind='spot',bids=[['99','2']],asks=[['100','.4'],['102','.6']],fee_rate='.001')
    fill=Book(b,1000).fill('1','buy')
    assert fill.cash==D('101.2')
    assert fill.fee==D('.1012')
    with pytest.raises(Invalid,match='DEPTH_LOW'):
        Book(b,1000).fill('1.001','buy')


def test_pair_gross_matches_fraction_reference_for_many_price_paths():
    entry=[dict(quantity='0.9',entry_price='100',side='buy'),
           dict(quantity='0.9',entry_price='102',side='sell')]
    for shift in range(-80,81,7):
        for spread in ('-1','.1','2','5'):
            a=Decimal(101+shift); b=a+D(spread)
            got=price_pnl(entry,[dict(exit_price=str(a)),dict(exit_price=str(b))])
            expected=Fraction(9,10)*((Fraction(102)-100)-(Fraction(b)-Fraction(a)))
            assert Fraction(got)==expected


@pytest.mark.parametrize('field,value,reason',[
    ('linear',False,'LINEAR_SETTLEMENT_REQUIRED'),
    ('settle','BTC','LINEAR_SETTLEMENT_REQUIRED'),
    ('asset_id','wrong','ASSET_OR_CURRENCY_MISMATCH'),
    ('quote_asset_id','wrapped-other','ASSET_OR_CURRENCY_MISMATCH'),
    ('identity_verified',False,'IDENTITY_VERIFIED_UNVERIFIED'),
    ('ts',980,'QUOTE_STALE'), ('fee_rate','NaN','NUMBER_INVALID'),
    ('multiplier',0,'POSITIVE_REQUIRED'), ('fee_rate',True,'NUMBER_INVALID')])
def test_missing_or_incompatible_data_never_opens(field,value,reason):
    s=snapshot();s['books'][1][field]=value
    result=evaluate(s,now=1000)
    assert not result['allowed']
    assert result['reason']==reason


def test_future_funding_is_not_free_money_on_ff():
    s=snapshot();s['funding_events']=[dict(instrument='a',ts=1500,rate='.05',notional_price='100')]
    assert evaluate(s,now=1000)['reason']=='SCENARIO_NET_LOW'
    s['funding_coverage_until']['a']=1100
    assert evaluate(s,now=1000)['reason']=='FUNDING_HORIZON_UNKNOWN'


def test_funding_sign_and_asynchronous_event_times():
    s=snapshot('perp_funding')
    s['funding_events']=[dict(instrument='a',ts=1100,rate='-.01',notional_price='100'),
                         dict(instrument='b',ts=1200,rate='.01',notional_price='102'),
                         dict(instrument='b',ts=2500,rate='-.9',notional_price='102')]
    r=evaluate(s,now=1000)
    assert r['allowed']
    assert D(r['plan']['funding_forecast'])==D('1.818')
    assert len(r['plan']['funding_scenario'])==2


def test_borrow_cost_and_availability():
    s=snapshot('reverse_carry')
    r=evaluate(s,now=1000)
    assert D(r['plan']['borrow_reserve'])==D('.0001275')
    s['borrow']['recalled']=True
    assert evaluate(s,now=1000)['reason']=='BORROW_UNAVAILABLE'


def test_inventory_uses_two_trading_fees_and_rebalance():
    r=evaluate(snapshot('spot_inventory'),now=1000)['plan']
    expected=D('.9')*D('2')-D('.9')*(100+102)*D('.0005')-D('.10')
    assert D(r['scenario_net'])==expected


@pytest.mark.parametrize('key', ['dated_carry','calendar','options_parity'])
def test_expiry_is_not_an_ordinary_perpetual(key):
    s=snapshot(key)
    i=0 if key=='calendar' else 1
    s['books'][i]['expiry']=999
    assert not evaluate(s,now=1000)['allowed']


def test_options_payoff_is_strike_for_all_terminal_prices():
    for terminal in (D('0'),D('1'),D('99'),D('100'),D('101'),D('1000000')):
        assert terminal+max(D(100)-terminal,D(0))-max(terminal-D(100),D(0))==100
    s=snapshot('options_parity');s['books'][2]['strike']='101'
    assert evaluate(s,now=1000)['reason']=='OPTION_PARITY_CONTRACT_MISMATCH'


def test_options_margin_is_not_just_the_call_premium():
    s=snapshot('options_parity')
    r=evaluate(s,now=1000)['plan']
    assert D(r['capital'])>180
    s['books'][2].pop('margin_verified')
    assert not evaluate(s,now=1000)['allowed']


@pytest.mark.parametrize('key', ['dex_perp','cex_dex_spot','cross_chain','dex_atomic'])
def test_dex_requires_explicit_zero_tax_and_known_gas(key):
    s=snapshot(key)
    dex=next(b for b in s['books'] if b['kind']=='dex')
    dex['transfer_tax_rate']='.01'
    assert evaluate(s,now=1000)['reason']=='TOKEN_MECHANICS_UNSUPPORTED'
    dex['transfer_tax_rate']='0';dex['gas_verified']=False
    assert evaluate(s,now=1000)['reason']=='GAS_VERIFIED_UNVERIFIED'


def test_triangle_reference_includes_each_fee_and_rounding_dust():
    s=snapshot('triangle')
    r=evaluate(s,now=1000)['plan']
    q1=D('8.995'); q2=q1
    amount_b=q2*2*(1-D('.0005'))
    q3=(amount_b//D('.001'))*D('.001')
    amount_a=q3*D('5.2')*(1-D('.0005'))
    dust_a=D(90)-q1*10*(1+D('.0005'))
    assert D(r['scenario_net'])==amount_a+dust_a-90
    assert D(r['dust']['B'])>=0
    s['books'][2]['asset_id']='counterfeit'
    assert evaluate(s,now=1000)['reason']=='CYCLE_TOKEN_IDENTITY_MISMATCH'


def test_atomic_guard_and_no_implicit_stablecoin_peg():
    s=snapshot('dex_atomic');s['atomic_min_output']='90'
    assert evaluate(s,now=1000)['reason']=='ATOMIC_MIN_PROFIT_INVALID'
    s=snapshot('stable_regional');s['subtype']='regional'
    assert evaluate(s,now=1000)['reason']=='REGIONAL_OBSERVE_ONLY'
    s=snapshot('stable_regional');s['start_asset']='USDC'
    assert evaluate(s,now=1000)['reason']=='ACCOUNT_CURRENCY_MISMATCH'
    s=snapshot('stable_regional');s['books'][1]['venue']='remote'
    assert evaluate(s,now=1000)['reason']=='CROSS_VENUE_CYCLE_REQUIRES_INVENTORY'


def test_immediate_roundtrip_cost_cannot_exceed_stop_at_admission():
    assert evaluate(snapshot(),dict(stop_loss='.01'),now=1000)['reason']=='IMMEDIATE_EXIT_LOSS_EXCEEDS_STOP'


def test_snapshot_cannot_claim_live_or_executable_evidence():
    s=snapshot();s['environment']='live'
    assert evaluate(s,now=1000)['reason']=='PAPER_REPLAY_EVIDENCE_REQUIRED'


def test_dex_quote_cannot_be_linearly_resized():
    s=snapshot('dex_perp');s['quantity']='.8'
    assert evaluate(s,now=1000)['reason']=='DEX_EXACT_SIZE_QUOTE_REQUIRED'


def test_current_bid_ask_friction_is_not_omitted_in_funding_scenario():
    s=snapshot('perp_funding')
    baseline=D(evaluate(s,now=1000)['plan']['scenario_net'])
    s['books'][0]['bids']=[['99.9','1000']]
    updated=evaluate(s,now=1000)['plan']
    # Additional .09 spread cost, partly offset by .000045 lower exit fee.
    assert baseline-D(updated['scenario_net'])==D('.089955')
