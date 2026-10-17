import asyncio
from types import SimpleNamespace as NS
from app.trading_controls import Controls
from app.paired_console import render


def test_product_gate_preserves_legacy_blocks_and_requires_opt_in_and_account_fee(tmp_path, monkeypatch):
    import app.main as main
    from app.risk import RiskGuard
    controls = Controls(tmp_path/'controls')
    controls.update(mode='auto')
    runtime = NS(enabled={'dex_paired': True})
    bundle = NS(inventory_paper=object(), dex_paired=NS(positions={}, state={}))
    monkeypatch.setattr(main, 'paired_paper', True)
    monkeypatch.setattr(main, 'secondary', bundle)
    monkeypatch.setattr(main, 'trading_controls', controls)
    monkeypatch.setattr(main, 'strategy_runtime', runtime)
    monkeypatch.setattr(main, 'risk', RiskGuard(250))
    monkeypatch.setattr(main, 'paper', NS(positions={}))
    row = dict(symbol='ETH/USDT:USDT',cex_venue='bybit',entry_cash=10,fee_source='ACCOUNT_READ_ONLY')
    assert main.operator_paper_reason('dex_paired', row) == 'PAIRED_PRODUCT_OPT_IN_REQUIRED'
    runtime.enabled['product_dex_paired'] = True
    assert main.operator_paper_reason('dex_paired', row) is None
    assert main.operator_paper_reason('dex_paired', dict(row, cex_venue='mexc')) == 'PAIRED_PRODUCT_VENUE_DISABLED'
    assert main.operator_paper_reason('dex_paired', dict(row, fee_source='PUBLIC_RATE_ASSUMPTION')) == 'PAIRED_PRODUCT_ACCOUNT_FEE_REQUIRED'
    assert main.operator_paper_reason('futures_futures', row) == 'RESEARCH_ONLY_USE_STRATEGY_LAB'
    controls.update(mode='manual')
    assert main.operator_paper_reason('dex_paired', row) == 'MANUAL_CONFIRMATION_REQUIRED'
    item=controls.claim(controls.issue('dex_paired',row))
    with controls.manual_intent(item):
        assert main.operator_paper_reason('dex_paired', row) is None


def test_console_off_still_shows_open_pairs_and_does_not_offer_live(tmp_path):
    engine=NS(positions={'1':{}},source=NS(routes=[{'cex_venue':'bybit'}]))
    text,keyboard=render(engine,{'dex_paired':True},Controls(tmp_path/'controls'),True)
    assert 'открыто: 1' in text and 'выключены' in text
    assert 'сопровождаются' in text
    callbacks={b.callback_data for row in keyboard.inline_keyboard for b in row}
    assert 'pair:toggle' in callbacks and 'pair:mode:manual' in callbacks
    assert not any('live' in c for c in callbacks)


def test_toggle_is_explicit_persisted_and_starts_manual_without_erasing_positions(tmp_path, monkeypatch):
    import app.main as main
    controls=Controls(tmp_path/'controls')
    controls.update(mode='auto')
    engine=NS(positions={'1': {'status':'OPEN'}},source=NS(routes=[]))
    runtime=NS(enabled={'dex_paired':True})
    saved=[]
    class Diary:
        async def record_decisions(self, rows): saved.extend(rows)
    class Query:
        data='pair:toggle';from_user=NS(id=1);message=object()
        async def answer(self,*a,**kw): pass
    async def edit(*a,**kw): pass
    monkeypatch.setattr(main,'allowed',lambda _:True)
    monkeypatch.setattr(main,'secondary',NS(dex_paired=engine))
    monkeypatch.setattr(main,'strategy_runtime',runtime)
    monkeypatch.setattr(main,'trading_controls',controls)
    monkeypatch.setattr(main,'strategy_toggles',NS(save=lambda state:saved.append(dict(state))))
    monkeypatch.setattr(main,'diary',Diary())
    monkeypatch.setattr(main,'safe_edit',edit)
    monkeypatch.setattr(main,'scanner',NS(paused=False))
    async def run():
        await main.paired_control_action(Query())
        assert controls.profile()['mode']=='manual'
        assert runtime.enabled['product_dex_paired'] and runtime.enabled['dex_paired']
        await main.paired_control_action(Query())
        assert not runtime.enabled['product_dex_paired'] and not runtime.enabled['dex_paired']
        assert engine.positions == {'1': {'status':'OPEN'}}
    asyncio.run(run())


def test_scanner_does_not_disguise_provider_denial_as_no_market_opportunities():
    from app.paired_console import scanner
    from collections import Counter
    engine=NS(clock=lambda:100)
    scan=NS(last_batch_seconds=.5,last_batch_at=100,last_sweep_seconds=4,
            catalog={1:{}},workers=3,checked=90,last_reasons=Counter(DEX_HTTP_403=3))
    service=NS(scanner=scan,candidates={})
    text,_=scanner(engine,service)
    assert 'HTTP 403' in text and 'Попыток проверки: 90' in text
    assert 'Обход списка: 4.0' in text
    assert 'Подходящих проверенных пар сейчас нет' not in text
    scan.last_reasons=Counter(DEX_HTTP_429=3)
    text,_=scanner(engine,service)
    assert 'лимит запросов' in text and 'Подходящих проверенных пар сейчас нет' not in text
    scan.last_reasons=Counter(PAIRED_PARTIAL_NET_LOW=3)
    text,_=scanner(engine,service)
    assert 'Подходящих проверенных пар сейчас нет' in text
