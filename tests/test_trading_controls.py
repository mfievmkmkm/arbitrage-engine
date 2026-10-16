import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from app.trading_controls import Controls
from app.discovery import RotatingUniverse
from app.public_book_pacing import Pacer, get


def test_environments_modes_and_risk_survive_restart_without_live_authority(tmp_path):
    path = tmp_path/'controls.json'
    c = Controls(path)
    c.update(mode='manual', notional_usd=25)
    c.update(environment='live')
    assert c.profile()['mode'] == 'observe'
    c.update(mode='auto', notional_usd=5)
    restored = Controls(path)
    assert restored.environment == 'live' and restored.profile('paper')['notional_usd'] == 25
    assert restored.entry_reason('paper', 'futures_futures') == 'OTHER_TRADING_ENVIRONMENT'
    restored.update(environment='paper')
    assert restored.entry_reason('paper', 'futures_futures', 'X') == 'MANUAL_CONFIRMATION_REQUIRED'
    assert 'live_enabled' not in path.read_text()


def test_manual_confirmation_is_one_shot_scoped_and_revoked_on_settings_change(tmp_path):
    c = Controls(tmp_path/'c.json')
    c.update(mode='manual')
    token = c.issue('futures_futures', dict(symbol='X'))
    item = c.claim(token)
    with c.manual_intent(item):
        assert c.entry_reason('paper', 'futures_futures', 'X') is None
        assert c.entry_reason('paper', 'futures_futures', 'Y')
        assert c.entry_reason('paper', 'dex_paired', 'X')
        c.update(min_net_usd=.5)
        assert c.entry_reason('paper', 'futures_futures', 'X')
    assert c.entry_reason('paper', 'futures_futures', 'X')
    with pytest.raises(ValueError, match='EXPIRED'):
        c.claim(token)


@pytest.mark.parametrize('elapsed', [-1, 31])
def test_expired_or_future_card_is_rejected(tmp_path, elapsed):
    now = [100]
    c = Controls(tmp_path/'c.json', clock=lambda:now[0])
    c.update(mode='manual')
    token = c.issue('futures_futures', dict(symbol='X'))
    now[0] += elapsed
    with pytest.raises(ValueError, match='EXPIRED'):
        c.claim(token)


@pytest.mark.parametrize('change', [dict(notional_usd=float('nan')), dict(mode='bad'),
    dict(max_positions=1.5), dict(daily_loss_usd=-1), dict(min_net_usd=True), dict(unknown=1)])
def test_invalid_risk_changes_do_not_replace_state(tmp_path, change):
    path = tmp_path/'c.json'
    c = Controls(path)
    c.update(mode='observe')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        c.update(**change)
    assert path.read_bytes() == before


@pytest.mark.parametrize('kwargs,expected', [
    (dict(notional=101, open_count=0, daily_net=0), 'OPERATOR_NOTIONAL_LIMIT'),
    (dict(notional=10, open_count=1, daily_net=0), 'OPERATOR_POSITION_LIMIT'),
    (dict(notional=10, open_count=0, daily_net=-5), 'OPERATOR_DAILY_STOP'),
    (dict(notional=10, open_count=0, daily_net=0, projected_net=.1), 'OPERATOR_NET_LOW'),
    (dict(notional=10, open_count=0, daily_net=0, spread_pct=11), 'OPERATOR_SPREAD_LIMIT')])
def test_risk_limits_have_specific_reasons(tmp_path, kwargs, expected):
    assert Controls(tmp_path/'c.json').risk_reason(**kwargs) == expected


def test_rotation_reaches_markets_beyond_original_window():
    symbols = {f'X{i:04d}' for i in range(301)}
    u = RotatingUniverse(dict(a=symbols, b=symbols), size=120, batch=30)
    seen = set()
    for _ in range(11):
        batch = u.next_batch()
        assert len(batch) == 30 and len(set(batch)) == 30
        seen.update(batch)
    assert seen == symbols and u.total_coverage == 301


def test_mexc_public_clients_share_a_pacer_and_preserve_other_venues():
    async def go():
        assert get(NS(id='mexc'), 'X/USDT:USDT') is get(NS(id='mexc'), 'Y/USDT:USDT')
        assert get(NS(id='mexc'), 'X/USDT') is not get(NS(id='mexc'), 'X/USDT:USDT')
        assert get(NS(id='bybit'), 'X/USDT:USDT') is None
    asyncio.run(go())


def test_pacing_spaces_requests_and_adds_backoff(monkeypatch):
    async def go():
        now, waits = [100], []
        async def sleep(delay):
            waits.append(delay)
            now[0] += delay
        monkeypatch.setattr('app.public_book_pacing.asyncio.sleep', sleep)
        p = Pacer(.15, clock=lambda:now[0])
        await p.wait()
        await p.wait()
        assert waits == pytest.approx([.15])
        p.limited()
        await p.wait()
        assert waits[-1] == pytest.approx(2)
        p.limited()
        await p.wait()
        assert waits[-1] == pytest.approx(4)
        p.success()
        p.limited()
        await p.wait()
        assert waits[-1] == pytest.approx(2)
    asyncio.run(go())


def test_mexc_default_pacing_stays_below_depth_endpoint_limit():
    # 10 requests / 2 seconds is the documented futures depth limit.
    assert Pacer().interval >= .2


def test_priority_book_preempts_scanner_without_exceeding_depth_budget():
    import time

    async def go():
        pacer = Pacer(.025)
        events = []

        async def take(name, priority):
            await pacer.wait(priority)
            events.append((name, time.monotonic()))

        await take('first', 1)
        tasks = [asyncio.create_task(take('scan-1', 1)),
                 asyncio.create_task(take('scan-2', 1))]
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(take('paired-mark', 0)))
        await asyncio.gather(*tasks)
        assert [name for name, _ in events] == ['first', 'paired-mark', 'scan-1', 'scan-2']
        assert all(b - a >= .022 for (_, a), (_, b) in zip(events, events[1:]))

    asyncio.run(go())


def test_cancelled_priority_waiter_does_not_block_other_books():
    async def go():
        pacer = Pacer(.02)
        await pacer.wait()
        cancelled = asyncio.create_task(pacer.wait(0))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await asyncio.wait_for(pacer.wait(), .2)

    asyncio.run(go())


def test_mexc_api_taker_fee_floor_matches_published_schedule():
    from app.engine import FEE_BPS
    from app.fee_schedule import DEFAULT_FUTURES_FEES
    assert FEE_BPS['mexc'] >= 8.0
    assert DEFAULT_FUTURES_FEES.require('mexc', 'taker') >= .0008
    assert DEFAULT_FUTURES_FEES.require('mexc', 'maker') >= .0006


def test_controls_view_names_units_and_marks_live_blocked(tmp_path):
    from app.trading_controls_view import render
    c = Controls(tmp_path/'c.json')
    c.update(environment='live')
    text, keyboard = render(c, False, show_risk=True)
    assert 'LIVE заблокирован' in text
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert any(b.callback_data == 'ctl:mode:observe' for b in buttons)
    assert any('Стоп закрытого NET' in b.text for b in buttons)
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


def test_exit_policy_is_tightened_and_snapshot_is_immutable(tmp_path):
    from app.trading_controls import operator_exit_limits
    c = Controls(tmp_path/'c.json')
    c.update(stop_loss_usd=.5,max_hold_seconds=300)
    policy = c.tighten_policy(dict(stop_usd=1,max_seconds=1200))
    c.update(stop_loss_usd=2,max_hold_seconds=1200)
    assert policy['stop_usd'] == .5 and policy['max_seconds'] == 300
    assert operator_exit_limits(dict(stop_loss_usd=1,max_hold_seconds=600),1200,-.5) == (600,-.5)
    assert operator_exit_limits(dict(stop_loss_usd=.25,max_hold_seconds=600),1200,-.5) == (600,-.25)
    with pytest.raises(ValueError):
        operator_exit_limits(dict(stop_loss_usd=float('nan'),max_hold_seconds=600),1200,-.5)


def test_manual_live_selection_cannot_submit_when_launch_flags_are_false(tmp_path, monkeypatch):
    async def go():
        import app.main as main
        c = Controls(tmp_path/'c.json')
        c.update(environment='live', mode='manual')
        token = c.issue('futures_futures', dict(symbol='X'))
        monkeypatch.setattr(main, 'trading_controls', c)
        monkeypatch.setattr(main, 'config', NS(admin_id=1, live_enabled=False, live_entry_enabled=False))
        messages = []
        async def edit(message, text, keyboard): messages.append(text)
        async def answer(*args, **kwargs): pass
        monkeypatch.setattr(main, 'safe_edit', edit)
        q = NS(data='enter:'+token, from_user=NS(id=1), answer=answer, message=object())
        await main.manual_entry(q)
        assert 'заблокирован' in messages[-1]
        assert token not in c.pending
    asyncio.run(go())


def test_manual_paper_refreshes_both_legs_and_observe_keeps_position(tmp_path, monkeypatch):
    async def go():
        import app.main as main
        from app.db import Diary
        from app.paper import PaperEngine
        from app.risk import RiskGuard
        from app.strategy_runtime import StrategyRuntime
        from test_paired_pipeline_regressions import scanner_with_books
        c = Controls(tmp_path/'controls.json')
        c.update(mode='manual')
        diary = Diary(tmp_path/'paper.db')
        await diary.init()
        engine = PaperEngine(diary,250)
        engine.paired_mode = True
        scanner = scanner_with_books()
        reads = []
        for venue, client in scanner.clients.items():
            client.market('X/USDT:USDT')['taker'] = .0005
            original = client.fetch_order_book
            async def book(*args, original=original, venue=venue, **kwargs):
                reads.append(venue)
                return await original(*args, **kwargs)
            client.fetch_order_book = book
        class Funding:
            async def get(self,*args):
                import time
                return NS(rate=.0001, next_ts=(time.time()+1000)*1000, interval_hours=8)
        scanner.funding = Funding()
        monkeypatch.setattr(main,'trading_controls',c)
        monkeypatch.setattr(main,'diary',diary)
        monkeypatch.setattr(main,'paper',engine)
        monkeypatch.setattr(main,'scanner',scanner)
        monkeypatch.setattr(main,'risk',RiskGuard(250))
        monkeypatch.setattr(main,'secondary',None)
        monkeypatch.setattr(main,'paired_paper',True)
        monkeypatch.setattr(main,'strategy_runtime',StrategyRuntime())
        monkeypatch.setattr(main,'venue_controller',NS(get=lambda v:dict(scan=True,paper=True)))
        monkeypatch.setattr(main,'config',NS(db_path=diary.path))
        row = dict(symbol='X/USDT:USDT',buy='binance',sell='bybit')
        item = c.claim(c.issue('futures_futures',row))
        with c.manual_intent(item):
            p,reason,fresh = await main.paper_futures_entry(row)
        assert p and reason == 'ENTRY_ALLOWED'
        assert set(reads) == {'binance','bybit'}
        assert p.base_qty == fresh['base_qty']
        c.update(mode='observe')
        assert main.paper_entry_reason(fresh) == 'OBSERVE_ONLY'
        assert p.id in engine.positions and engine.used_capital > 0
    asyncio.run(go())
