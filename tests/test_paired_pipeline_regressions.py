import asyncio
import json
import sqlite3
import time
import zipfile
from types import SimpleNamespace as NS

import pytest

from app.db import Diary
from app.engine import Scanner
from app.discovery import RotatingUniverse
from app.instruments import from_market
from app.funding_service import FundingService
from app.paired_futures_funding import attach
from app.paired_paper_policy import exit_reason, plan
from app.compact_audit_export import build_sync
from app.scan_decisions import skipped_net_candidates
from app.scan_diagnostics import render
from test_native_order_plan import client, SYMBOL


def scanner_with_books(spread=2):
    scanner = Scanner(['binance', 'bybit', 'okx'], 100, 12, concurrency=4)
    for i, venue in enumerate(scanner.ids):
        c = client()
        async def book(*args, offset=i * spread, **kwargs):
            return dict(bids=[[100 + offset, 10000]], asks=[[100.01 + offset, 10000]])
        c.fetch_order_book = book
        scanner.clients[venue] = c
    scanner.symbols = {v: {SYMBOL} for v in scanner.ids}
    scanner.specs = {v: {SYMBOL: from_market(v, c.market(SYMBOL))}
                     for v, c in scanner.clients.items()}
    scanner.universe = RotatingUniverse(scanner.symbols)
    return scanner


def test_empty_cycle_has_persistent_diagnostics_and_compact_export(tmp_path):
    async def go():
        scanner = scanner_with_books(spread=0)
        assert await scanner.scan() == []
        summary = scanner.last_diagnostics
        assert summary['symbols_selected'] == 1
        assert summary['venues_configured'] == ['binance', 'bybit', 'okx']
        assert summary['venues_online'] == 3
        assert summary['venue_start_errors'] == {}
        assert summary['counters']['books_ok'] == 3
        assert summary['counters']['pairs_checked'] == 6
        assert summary['counters']['pairs_no_edge_before_funding'] == 6
        assert 'пар 6' in render(scanner)
        diary = Diary(tmp_path / 'journal.db')
        await diary.init()
        await diary.record_scan(summary)
        with sqlite3.connect(diary.path) as db:
            saved = json.loads(db.execute('SELECT payload FROM scan_cycles').fetchone()[0])
            assert saved == summary
        with zipfile.ZipFile(build_sync(diary.path, tmp_path / 'out')) as archive:
            assert 'scan_cycles.csv' in archive.namelist()
            assert json.loads(archive.read('manifest.json'))['tables']['Scan cycles']['total_rows'] == 1
    asyncio.run(go())


def test_post_funding_net_rejection_survives_compact_export(tmp_path):
    async def go():
        diary = Diary(tmp_path / 'journal.db')
        await diary.init()
        rows = [dict(ts=123, symbol=SYMBOL, buy='bybit', sell='mexc',
                     hypothetical_edge=-.04, funding_known=True,
                     funding_pct=-.25, executable=.40),
                dict(ts=124, symbol='Y/USDT:USDT', buy='mexc', sell='bybit',
                     hypothetical_edge=.12)]
        rejected = skipped_net_candidates(rows, paired_paper=True)
        assert len(rejected) == 1 and rejected[0]['action'] == 'SKIP'
        assert rejected[0]['reason'] == 'NET_ESTIMATE_NOT_POSITIVE'
        await diary.record_decisions(rejected)
        with zipfile.ZipFile(build_sync(diary.path, tmp_path / 'out')) as archive:
            import csv, io
            exported = list(csv.DictReader(io.StringIO(archive.read('decisions.csv').decode())))
        assert len(exported) == 1
        assert exported[0]['symbol'] == SYMBOL
        assert exported[0]['reason'] == 'NET_ESTIMATE_NOT_POSITIVE'

    asyncio.run(go())


def test_scanner_funding_enrichment_is_bounded_and_parallel():
    async def go():
        scanner = scanner_with_books()
        active = peak = 0
        both = asyncio.Event()
        class Funding:
            async def pair_carry_pct(self, *args):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == 2:
                    both.set()
                try:
                    await asyncio.wait_for(both.wait(), .5)
                    await asyncio.sleep(0)
                    return 0, True, 'PER_LEG_CALENDAR'
                finally:
                    active -= 1
        scanner.funding = Funding()
        rows = await scanner.scan()
        assert len(rows) == 3
        assert peak == 2 and all(r['funding_known'] for r in rows)
    asyncio.run(go())


def test_concurrent_same_funding_key_fetches_only_once_and_refreshes_at_settlement(monkeypatch):
    async def go():
        calls = 0
        now = [1000]
        monkeypatch.setattr('app.funding_service.time.time', lambda: now[0])
        async def fetch(symbol):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return dict(fundingRate=.001, fundingTimestamp=(now[0]+10)*1000, interval='8h')
        service = FundingService({'bybit': NS(has={'fetchFundingRate': True}, fetch_funding_rate=fetch)})
        snapshots = await asyncio.gather(*(service.get('bybit', SYMBOL) for _ in range(8)))
        assert calls == 1 and len(snapshots) == 8
        now[0] += 11  # less than TTL, but the cached next event is in the past
        assert (await service.get('bybit', SYMBOL)).next_ts == 1021000
        assert calls == 2
    asyncio.run(go())


def test_slow_funding_precedes_fresh_executable_books(monkeypatch):
    from app.paired_futures_quotes import refresh
    async def go():
        now = [time.time()]
        monkeypatch.setattr('app.paired_futures_quotes.time.time', lambda: now[0])
        scanner = scanner_with_books()
        events = []
        for c in scanner.clients.values():
            c.market(SYMBOL)['taker'] = .0005
            original = c.fetch_order_book
            async def book(*args, original=original, **kw):
                events.append('book')
                return await original(*args, **kw)
            c.fetch_order_book = book
        class Funding:
            async def get(self, *args):
                events.append('funding')
                now[0] += 3
                return NS(next_ts=(now[0]+1000)*1000, interval_hours=8, rate=.0001)
        scanner.funding = Funding()
        row = await refresh(dict(symbol=SYMBOL, buy='binance', sell='bybit'), scanner)
        assert events == ['funding', 'funding', 'book', 'book']
        assert row['funding_known'] and row['native_plan']['valid']
        assert now[0] - row['ts'] < 1.5
    asyncio.run(go())


def test_attach_clears_old_calendar_and_does_not_retain_verified_flag():
    async def go():
        row = dict(buy='a', sell='b', symbol=SYMBOL, entry_buy=100, entry_sell=102,
                   base_qty=1, funding_known=True, funding_reserve_usd=9)
        await attach(row, None, snapshots=[NS(next_ts=None), NS(next_ts=None)])
        assert row['funding_calendar'] is None
        assert row['funding_known'] is False and row['funding_reserve_usd'] == 0
    asyncio.run(go())


@pytest.mark.parametrize('known,net,expected', [(True, -1.2, 'NET_STOP'),
                                             (False, -1.2, None),
                                             (True, -.2, None)])
def test_verified_funding_loss_participates_in_pair_stop(known, net, expected):
    policy = plan(100, 2, .3)
    mark = dict(ts=10, gross=0, entry_fees=.1, exit_fees=.1, safety=0, net=net)
    assert exit_reason(policy, 1, 0, mark, known) == expected


def test_funding_income_does_not_remove_price_only_emergency_stop():
    mark = dict(ts=10, gross=-1.5, entry_fees=.1, exit_fees=.1, safety=0, net=.5)
    assert exit_reason(plan(100, 2, .3), 1, 0, mark, True) == 'NET_STOP'


def test_refreshed_pair_opens_restarts_and_exits_on_verified_funding_loss(tmp_path):
    from app.paired_futures_quotes import refresh
    from app.paper import PaperEngine
    from app.funding_paper_source import History
    async def go():
        scanner = scanner_with_books()
        for c in scanner.clients.values():
            c.market(SYMBOL)['taker'] = .0005
            async def forbidden(*args, **kw):
                raise AssertionError('Paper must not submit real orders or transfers')
            c.create_order = c.transfer = c.withdraw = forbidden
        class Funding:
            async def get(self, *args):
                return NS(next_ts=(time.time()+1000)*1000, interval_hours=8, rate=.0001)
        scanner.funding = Funding()
        row = await refresh(dict(symbol=SYMBOL, buy='binance', sell='bybit'), scanner)
        diary = Diary(tmp_path/'paper.db')
        await diary.init()
        engine = PaperEngine(diary, 250)
        engine.paired_mode = True
        p = await engine.open(row)
        assert p and engine.used_capital > 195
        restored = PaperEngine(diary, 250)
        restored.paired_mode = True
        await restored.restore()
        assert restored.positions[p.id].base_qty == p.base_qty
        async def settled(position, until):
            return History(True, -1.25, (), 'PUBLIC_HISTORY', until)
        restored.funding_history = settled
        mark = await refresh(dict(symbol=SYMBOL, buy='binance', sell='bybit'), scanner, restored.positions[p.id])
        closed = await restored.mark_and_exit([mark])
        assert len(closed) == 1 and closed[0].current_net_usd < -1.25
        assert restored.used_capital == 0
        with sqlite3.connect(diary.path) as db:
            assert db.execute('SELECT close_reason FROM paper_positions').fetchone()[0] == 'NET_STOP'
    asyncio.run(go())


def test_audit_uses_manifest_coverage_and_reports_empty_cycles(tmp_path):
    from tools.audit_paper_export import analyze
    async def go():
        diary = Diary(tmp_path/'audit.db')
        await diary.init()
        scanner = scanner_with_books(spread=0)
        await scanner.scan()
        await diary.record_scan(scanner.last_diagnostics)
        await diary.record_scan(dict(ts=time.time()+1, status='FAILED'))
        # A single exported decision can still be a truncated sample.
        await diary.record_decisions([dict(ts=i, strategy='futures_futures', symbol=SYMBOL,
                                          buy='binance', sell='bybit', reason='LIMIT', action='SKIP') for i in (1, 2)])
        report = analyze(build_sync(diary.path, tmp_path/'out', row_limit=1))
        assert report['decisions']['decisions.csv']['possibly_capped'] is True
        assert report['decisions']['decisions.csv']['total_rows'] == 2
        assert report['scan_cycles']['coverage']['truncated'] is True
        assert report['scan_cycles']['statuses'] == {'FAILED': 1}
        assert report['scan_cycles']['latest']['status'] == 'FAILED'
    asyncio.run(go())


@pytest.mark.parametrize('strategy,flag', [('spot_spot', 'live_spot_spot_enabled'),
                                        ('spot_futures', 'live_spot_futures_enabled'),
                                        ('funding_arb', 'live_funding_enabled')])
def test_paper_toggles_cannot_dispatch_secondary_live_entries(strategy, flag):
    from app.secondary_strategy_runtime import live_dispatch_enabled
    config = NS(live_enabled=False, live_entry_enabled=True, **{flag: True})
    assert not live_dispatch_enabled(config, strategy)
    config.live_enabled = True
    config.live_entry_enabled = False
    assert not live_dispatch_enabled(config, strategy)
    config.live_entry_enabled = True
    assert live_dispatch_enabled(config, strategy)
    setattr(config, flag, False)
    assert not live_dispatch_enabled(config, strategy)
    assert not live_dispatch_enabled(config, 'unknown')
