import asyncio
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from app.cex_dex_paper import Engine
from app.dex_paired_experiment import PairedSource
from app.zerox_research import Provider
from test_dex_virtual_paper import source, STABLE, ASSET


class Response:
    def __init__(self, status, payload=None, headers=None, on_json=None):
        self.status, self.payload = status, payload
        self.headers, self.on_json = headers or {}, on_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        if self.on_json:
            self.on_json()
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def quote_response(**changes):
    return dict(liquidityAvailable=True, sellToken=STABLE, buyToken=ASSET,
                sellAmount='100', buyAmount='200', **changes)


def test_zerox_age_starts_at_request_not_at_response():
    async def go():
        now = [100]
        session = Session(Response(200, quote_response(), on_json=lambda: now.__setitem__(0, 105)))
        provider = Provider('not-a-real-key', session, clock=lambda: now[0])
        row = await provider.price(1, STABLE, ASSET, '100')
        assert row['ok'] and row['ts'] == 100
        assert not row['live_allowed'] and now[0] - row['ts'] == 5
    asyncio.run(go())


def test_zerox_slow_response_is_not_accepted_as_fresh():
    async def go():
        now = [100]
        session = Session(Response(200, quote_response(), on_json=lambda: now.__setitem__(0, 109)))
        provider = Provider('not-a-real-key', session, clock=lambda: now[0])
        assert (await provider.price(1, STABLE, ASSET, '100'))['reason'] == 'DEX_PRICE_RESPONSE_STALE'
    asyncio.run(go())


def test_zerox_403_cause_survives_into_paper_diagnostics():
    from app.dex_virtual_paper_source import PriceUnavailable

    async def go():
        session = Session(Response(403, {'code': 'TAKER_NOT_AUTHORIZED_FOR_TRADE'}))
        provider = Provider('test-only', session)
        result = await provider.price(1, STABLE, ASSET, '100')
        assert result['reason'] == 'DEX_HTTP_403'
        assert PriceUnavailable(result).diagnostics['error_code'] == 'TAKER_NOT_AUTHORIZED_FOR_TRADE'
    asyncio.run(go())


@pytest.mark.parametrize('status', [401, 403, 503])
def test_http_failures_back_off_without_bypassing_access_or_faking_quotes(status):
    async def go():
        now = [100]
        session = Session(Response(status), Response(200, quote_response()))
        provider = Provider('not-a-real-key', session, clock=lambda: now[0])
        first = await provider.price(1, STABLE, ASSET, '100')
        second = await provider.price(1, STABLE, ASSET, '100')
        assert not first['ok'] and not second['ok']
        assert second['reason'] == f'DEX_HTTP_{status}'
        assert second['request_sent'] is False and session.calls == 1
        now[0] += first['retry_after_seconds'] + 1
        assert (await provider.price(1, STABLE, ASSET, '100'))['ok']
        assert session.calls == 2
    asyncio.run(go())


def test_rate_limit_is_shared_across_chains_and_retry_after_is_bounded():
    async def go():
        now = [100]
        session = Session(Response(429, headers={'Retry-After': '3600'}), Response(200, quote_response()))
        provider = Provider('not-a-real-key', session, clock=lambda: now[0])
        first = await provider.price(1, STABLE, ASSET, '100')
        assert first['retry_after_seconds'] == 60
        blocked = await provider.price(56, STABLE, ASSET, '100')
        assert blocked['reason'] == 'DEX_HTTP_429' and session.calls == 1
        now[0] += 61
        assert (await provider.price(56, STABLE, ASSET, '100'))['ok']
    asyncio.run(go())


def test_zerox_auth_failure_blocks_other_chains_and_records_safe_error_code():
    async def go():
        now=[100]
        session=Session(Response(403, {'code':'TAKER_NOT_AUTHORIZED_FOR_TRADE'}),
                        Response(200, quote_response()))
        provider=Provider('not-a-real-key',session,clock=lambda:now[0])
        first=await provider.price(1,STABLE,ASSET,'100')
        assert first['reason']=='DEX_HTTP_403'
        assert first['error_code']=='TAKER_NOT_AUTHORIZED_FOR_TRADE'
        blocked=await provider.price(42161,STABLE,ASSET,'100')
        assert blocked['reason']=='DEX_HTTP_403' and not blocked['request_sent']
        assert blocked['action_required']=='CHECK_API_ACCESS' and session.calls==1
        now[0]+=31
        assert (await provider.price(42161,STABLE,ASSET,'100'))['ok']
    asyncio.run(go())


def test_invalid_inputs_do_not_create_requests():
    async def go():
        session = Session()
        provider = Provider('not-a-real-key', session)
        for chain, sell, amount in [(True, STABLE, '100'), (1.5, STABLE, '100'), (1, None, '100'), (1, STABLE, '-1')]:
            assert not (await provider.price(chain, sell, ASSET, amount))['ok']
        assert session.calls == 0
    asyncio.run(go())


def paired_source_with_rate():
    original, route, public, provider, now = source('paired')
    route.update(notional_usd=100, max_notional_usd=110)
    public.currencies = {'X': dict(code='X', networks={'ETH': dict(contract=ASSET)})}
    calls = []
    class Funding:
        async def get(self, *args):
            calls.append('funding')
            now[0] += 3
            return NS(rate=.0001, next_ts=1000000, interval_hours=8)
    original.fs = Funding()
    return PairedSource(original, [route]), route, public, provider, now, calls


def test_slow_funding_and_reverse_quote_do_not_age_entry_gas_book():
    async def go():
        s, route, public, provider, now, calls = paired_source_with_rate()
        price = provider.price
        async def slow(*args):
            now[0] += 2
            return await price(*args)
        provider.price = slow
        result = await s.entry(route)
        assert result['ok'], result
        p = result['position']
        assert calls == ['funding']
        assert now[0] - p['entry_gas_book']['timestamp']/1000 <= 1.5
        assert now[0] - p['entry_cex']['book_ts'] <= 1.5
        assert p['entry_gas'] > 0 and p['entry_fees'] > p['entry_gas']
        assert p['paired_policy']['funding_reserve'] == 0
    asyncio.run(go())


def test_monitor_outage_is_persisted_reservation_survives_restart_then_recovers(tmp_path):
    async def go():
        s, route, public, provider, now, calls = paired_source_with_rate()
        path = tmp_path/'dex.db'
        e = Engine(path, s, capital=250, virtual=True, namespace='paired', clock=s.clock)
        await e.init()
        await e.cycle(route)
        p = next(iter(e.positions.values()))
        # No cost-free zero-PnL interval after a new Paper entry.
        assert p['net'] == p['entry_immediate_net'] < 0
        reserve = e.used_capital
        price = provider.price
        async def denied(*args):
            return dict(ok=False, reason='DEX_HTTP_403', request_sent=False, retry_after_seconds=20)
        provider.price = denied
        now[0] += 10
        assert not await e.cycle(entry_enabled=False)
        p = next(iter(e.positions.values()))
        assert p['data_reason'] == 'DEX_HTTP_403' and e.used_capital == reserve
        with sqlite3.connect(path) as db:
            row = db.execute('SELECT reason,payload FROM cex_dex_virtual_paper_paired_decisions ORDER BY id DESC LIMIT 1').fetchone()
            assert row[0] == 'DEX_HTTP_403'
            assert json.loads(row[1])['diagnostics']['retry_after_seconds'] == 20
            assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 0
        restored = Engine(path, s, capital=250, virtual=True, namespace='paired', clock=s.clock)
        await restored.init()
        assert restored.used_capital == reserve
        assert next(iter(restored.positions.values()))['data_unavailable_since'] == p['data_unavailable_since']
        provider.price = price
        public.bid = 1.002
        now[0] += 10
        closed = await restored.cycle(entry_enabled=False)
        assert len(closed) == 1 and closed[0]['net'] > 0
        assert restored.used_capital == 0
    asyncio.run(go())


def test_funding_exception_does_not_prevent_price_risk_exit_or_fabricate_final_net(tmp_path):
    async def go():
        s, route, public, provider, now, calls = paired_source_with_rate()
        e = Engine(tmp_path/'funding.db', s, capital=250, virtual=True, namespace='paired', clock=s.clock)
        await e.init()
        await e.cycle(route)
        history = s.settlements
        async def unavailable(*args):
            raise RuntimeError('provider failed')
        s.settlements = unavailable
        public.bid = 1.10
        now[0] += 10
        assert not await e.cycle(entry_enabled=False)
        p = next(iter(e.positions.values()))
        assert p['status'] == 'EXIT_ACCOUNTING_PENDING'
        assert p['exit_reason'] == 'NET_STOP'
        frozen = p['exit_quote']
        s.settlements = history
        now[0] += 5
        public.bid = .5
        closed = await e.cycle(entry_enabled=False)
        assert len(closed) == 1 and closed[0]['exit_quote'] == frozen
        assert closed[0]['net'] < -1
    asyncio.run(go())


def test_migration_preserves_paper_pause_and_marker(tmp_path):
    from app.prepare_paper_server import prepare, verify_bundle
    from app.configure_virtual_paper import default_routes
    from app.paired_paper_profile import load_pause, migrate_toggles
    from app.strategy_toggle_store import Store
    from app.strategy_runtime import StrategyRuntime
    path = tmp_path/'source.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE history(value TEXT)')
        db.execute("INSERT INTO history VALUES('keep losses')")
    state = tmp_path/'runtime_state.json'
    marker = dict(version=1, old_history_preserved=True)
    (tmp_path/'runtime_state.json.paired_migration.json').write_text(json.dumps(marker))
    (tmp_path/'runtime_state.json.paper_controls.json').write_text('{"paused":true}')
    (tmp_path/'runtime_state.json.strategies.json').write_text('{"futures_futures":false,"dex_paired":false}')
    env = tmp_path/'bot.env'
    env.write_text('BOT_TOKEN=placeholder\nLIVE_ENABLED=false\nDEX_VIRTUAL_PAPER_ENABLED=true\n'
                   + 'DB_PATH=' + str(path) + '\nRUNTIME_STATE_PATH=' + str(state) + '\n'
                   + 'DEX_VIRTUAL_ROUTES_JSON=' + json.dumps(default_routes('reference')) + '\n')
    target = prepare(env, tmp_path/'transfer')
    assert verify_bundle(target)
    migrated = target/'data/runtime_state.json'
    assert load_pause(migrated) is True
    runtime = StrategyRuntime()
    store = Store(str(migrated)+'.strategies.json')
    runtime.enabled.update(store.load({}))
    migrate_toggles(migrated, runtime, store)
    # Versioned owner-requested PAPER convergence activation runs only once.
    assert runtime.enabled['futures_futures'] and not runtime.enabled['dex_paired']
    with sqlite3.connect(target/'data/arbitrage.sqlite3') as db:
        assert db.execute('SELECT value FROM history').fetchone()[0] == 'keep losses'


def test_access_backoff_grows_caps_and_recovers_with_separate_http_counters():
    async def run():
        now=[100]
        session=Session(*(Response(403) for _ in range(8)),Response(200,quote_response()))
        p=Provider('test-only',session,clock=lambda:now[0])
        delays=[]
        for n in range(8):
            denied=await p.price(1,STABLE,ASSET,'100')
            delays.append(denied['retry_after_seconds'])
            async def forbidden():raise AssertionError('Known block must not queue')
            acquire=p.budget.acquire;p.budget.acquire=forbidden
            blocked=await p.price(56,STABLE,ASSET,'100')
            p.budget.acquire=acquire
            assert not blocked['request_sent'] and session.calls==n+1
            assert p.health()['requests_sent']==n+1
            now[0]+=denied['retry_after_seconds']+1
        assert delays==[30,60,120,240,480,960,1800,1800]
        assert (await p.price(1,STABLE,ASSET,'100'))['ok']
        health=p.health()
        assert not health['blocked'] and health['responses_ok']==1
        assert health['requests_sent']==9 and health['suppressed']==8
        assert p.access_failures==0 and p.access_error_code is None
    asyncio.run(run())


def test_operator_access_probe_is_throttled_and_does_not_clear_rate_limit():
    async def run():
        now=[100]
        p=Provider('test-only',Session(Response(403)),clock=lambda:now[0])
        await p.price(1,STABLE,ASSET,'100')
        assert not p.request_access_recheck()
        now[0]+=31
        assert p.request_access_recheck() and p.access_block_until==0
        assert not p.request_access_recheck()
        now[0]+=31;p.rate_limit_until=now[0]+60
        assert not p.request_access_recheck()
        assert p.rate_limit_until==now[0]+60
    asyncio.run(run())
