import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.build_virtual_watchlist import SEEDS, USDC, generate
from app.dex_virtual_paper_source import routes_checked, route_key
from app.jupiter_research import Provider, mint_valid, Router
from app.prepare_paper_server import prepare, verify_bundle
from app.paper_server import validate


class Response:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data


class Session:
    def __init__(self, data, status=200):
        self.data, self.status, self.calls = data, status, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.data, self.status)


def quote(**changes):
    return dict(inputMint=USDC, outputMint=SEEDS["SOL"], inAmount="5000000",
                outAmount="25000000", otherAmountThreshold="24950000", swapMode="ExactIn",
                slippageBps=20, priceImpact=-.05, routePlan=[dict(percent=100)], **changes)


def test_quote_only_http_contract_preserves_solana_case_and_has_no_wallet():
    async def go():
        session = Session(quote())
        result = await Provider("placeholder", session, clock=lambda: 100).price("solana", USDC, SEEDS["SOL"], "5000000")
        assert result["ok"] and result["ts"] == 100
        assert result["network_fee_raw"] is None and not result["live_allowed"]
        url, call = session.calls[0]
        assert url == "https://api.jup.ag/swap/v2/order"
        assert call["params"]["inputMint"] == USDC
        assert not {"taker", "receiver"} & call["params"].keys()
        assert call["params"]["slippageBps"] == "20"
        assert result["price_impact_pct"] == "-0.0005"
    asyncio.run(go())


@pytest.mark.parametrize("field,value,reason", [
    ("inputMint", USDC.lower(), "SCOPE"),
    ("inAmount", "5000001", "SCOPE"),
    ("swapMode", "ExactOut", "SCOPE"),
    ("otherAmountThreshold", "25000001", "AMOUNT"),
    ("outAmount", "0", "AMOUNT"),
    ("priceImpact", -2, "IMPACT"),
    ("priceImpact", "nan", "IMPACT"),
    ("slippageBps", 50, "SLIPPAGE"),
    ("routePlan", [], "ROUTE"),
])
def test_unsafe_or_mismatched_quotes_are_rejected(field, value, reason):
    async def go():
        raw = quote()
        raw[field] = value
        x = await Provider("placeholder", Session(raw)).price("solana", USDC, SEEDS["SOL"], "5000000")
        assert not x["ok"] and reason in x["reason"]
    asyncio.run(go())


def test_missing_api_key_makes_no_http_call():
    session = Session(quote())
    result = asyncio.run(Provider(None, session).price("solana", USDC, SEEDS["SOL"], "5000000"))
    assert result["reason"] == "JUPITER_API_KEY_MISSING" and not session.calls


def test_rate_limit_cooldown_fails_fast_without_delaying_risk_exit():
    async def go():
        session = Session({}, status=429)
        p = Provider("placeholder", session)
        args = ("solana", USDC, SEEDS["SOL"], "5000000")
        assert (await p.price(*args))["reason"] == "JUPITER_HTTP_429"
        assert (await asyncio.wait_for(p.price(*args), .5))["reason"] == "JUPITER_RATE_LIMIT_COOLDOWN"
        assert len(session.calls) == 1
    asyncio.run(go())


def test_solana_route_validation_and_case_sensitive_identity():
    r = dict(chain_id="solana", stable=USDC, asset=SEEDS["SOL"], stable_decimals=6,
             asset_decimals=9, base="SOL", cex_venue="bybit", cex_symbol="SOL/USDT:USDT",
             native_symbol="SOL/USDT:USDT", native_decimals=9, mode="reference", notional_usd=20)
    assert routes_checked([r]) and route_key(r)[1] == USDC
    assert all(mint_valid(x) for x in SEEDS.values())
    assert not mint_valid("0x" + "1" * 40)
    with pytest.raises(ValueError, match="REFERENCE_ONLY"):
        routes_checked([dict(r, mode="paired")])
    with pytest.raises(ValueError, match="SLIPPAGE"):
        routes_checked([dict(r, slippage_bps=20.5)])


def test_watchlist_uses_only_exact_native_markets_preserves_existing_routes():
    from app.configure_virtual_paper import default_routes
    async def go():
        m = dict(symbol="SOL/USDT:USDT", base="SOL", quote="USDT", settle="USDT",
                 swap=True, linear=True, active=True, contractSize=1)
        clients = {"bybit": SimpleNamespace(markets={"SOL": m, "scaled": dict(m, base="1000GP")})}

        class P:
            async def token(self, mint):
                return dict(id=mint, symbol="USDC" if mint == USDC else "SOL", decimals=6 if mint == USDC else 9)

            async def price(self, chain, sell, buy, amount):
                return dict(ok=True, min_buy_amount_raw="123456")
        existing = default_routes("reference")
        result, report = await generate(existing, clients, P(), {k: SEEDS[k] for k in ("SOL", "GP")})
        assert result[0] == existing[0] and len(result) == 2
        assert result[1]["asset"] == SEEDS["SOL"] and result[1]["stable_symbol"] == "USDC"
        assert report[1]["reason"] == "CEX_LINEAR_USDT_MARKET_MISSING"
        result2, _ = await generate(result, clients, P(), {"SOL": SEEDS["SOL"]})
        assert result2 == result
    asyncio.run(go())


def test_watchlist_rejects_wrong_mint_metadata_instead_of_guessing():
    from app.configure_virtual_paper import default_routes
    async def go():
        client = SimpleNamespace(markets={"x": dict(symbol="SOL/USDT:USDT", base="SOL", quote="USDT", settle="USDT", swap=True, linear=True, active=True, contractSize=1)})

        class P:
            async def token(self, mint):
                return dict(symbol="USDC" if mint == USDC else "FAKE", decimals=6)
        result, report = await generate(default_routes("reference"), {"bybit": client}, P(), {"SOL": SEEDS["SOL"]})
        assert len(result) == 1 and report[0]["reason"] == "CONFIGURED_MINT_SYMBOL_MISMATCH"
    asyncio.run(go())


def test_solana_source_can_open_restore_and_close_with_no_orders(tmp_path):
    from test_dex_virtual_paper import source
    from app.cex_dex_paper import Engine
    async def go():
        s, r, public, old_prices, now = source("reference")
        r.update(chain_id="solana", stable=USDC, asset=SEEDS["SOL"], stable_symbol="USDC")

        class P:
            async def price_route(self, r, sell_asset, amount):
                raw = int(amount)
                return dict(ok=True, chain_id="solana", sell_token=r["asset"] if sell_asset else r["stable"],
                            buy_token=r["stable"] if sell_asset else r["asset"], sell_amount_raw=str(amount),
                            buy_amount_raw=str(raw * 10000 if sell_asset else raw // 10000), ts=now[0])
        s.provider = P()
        e = Engine(tmp_path / "solana.db", s, clock=s.clock, virtual=True)
        await e.init()
        await e.cycle(r)
        assert e.positions and e.state["checked_count"] == 1
        p = next(iter(e.positions.values()))
        assert p["chain_id"] == "solana" and p["entry_gas"] == 0
        restored = Engine(e.path, s, clock=s.clock, virtual=True)
        await restored.init()
        assert restored.positions == e.positions
        now[0] += 10
        public.bid = 1.002
        assert (await restored.cycle(entry_enabled=False))[0]["net"] > 0
    asyncio.run(go())


def test_server_migration_preserves_database_keys_and_companion_states(tmp_path):
    from app.configure_virtual_paper import default_routes
    db = tmp_path / "custom.db"
    with sqlite3.connect(db) as d:
        d.execute("CREATE TABLE history(value TEXT)")
        d.execute("INSERT INTO history VALUES('original')")
    env = tmp_path / ".env"
    text = "BOT_TOKEN=placeholder\nBYBIT_API_SECRET=private-placeholder\nDEX_VIRTUAL_PAPER_ENABLED=true\nDB_PATH=custom.db\nRUNTIME_STATE_PATH=my-state.json\nDEX_VIRTUAL_ROUTES_JSON=" + json.dumps(default_routes("reference")) + "\n"
    env.write_text(text)
    (tmp_path / "my-state.json.strategies.json").write_text('{"cex_dex": true}')
    bundle = prepare(env, tmp_path / "transfer")
    assert (bundle / "bot.env").read_text() == text
    assert json.loads((bundle / "data/runtime_state.json.strategies.json").read_text()) == {"cex_dex": True}
    with sqlite3.connect(bundle / "data/arbitrage.sqlite3") as d:
        assert d.execute("SELECT value FROM history").fetchone()[0] == "original"
    assert json.loads((bundle / "manifest.json").read_text())["table_rows"]["history"] == 1
    assert verify_bundle(bundle)
    (bundle / "data/runtime_state.json.strategies.json").write_text('{}')
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        verify_bundle(bundle)
    with pytest.raises(ValueError, match="EXISTS"):
        prepare(env, bundle)
    assert env.read_text() == text


@pytest.mark.parametrize("key", ["LIVE_ENABLED", "LIVE_ENTRY_ENABLED", "DEX_LIVE_ENABLED"])
def test_server_refuses_live_flags(key):
    with pytest.raises(ValueError, match="LIVE_FLAGS"):
        validate(dict(BOT_TOKEN="placeholder", DEX_VIRTUAL_PAPER_ENABLED="true", **{key: "true"}))


def test_0x_virtual_requests_explicit_slippage_instead_of_api_default():
    from app.zerox_research import Provider as EVM
    async def go():
        sell, buy = "0x" + "1" * 40, "0x" + "2" * 40
        session = Session(dict(liquidityAvailable=True, sellToken=sell, buyToken=buy,
                               sellAmount="5000000", buyAmount="1000"))
        result = await EVM("placeholder", session).price(1, sell, buy, "5000000", slippage_bps=20)
        assert result["ok"]
        assert session.calls[0][1]["params"]["slippageBps"] == "20"
    asyncio.run(go())


def test_virtual_service_uses_own_interval_without_speeding_other_services():
    from app.secondary_strategy_runtime import SecondaryRuntime
    from app.strategy_runtime import StrategyRuntime
    async def go():
        enough = asyncio.Event()
        class S:
            def __init__(self):
                self.calls = 0

            async def cycle(self):
                self.calls += 1
                if self.calls >= 3:
                    enough.set()
                return []
        fast, normal = S(), S()
        fast.interval = .001

        async def record(*args):
            pass
        state = StrategyRuntime()
        state.enabled.update(cex_dex=True, spot_spot=True)
        runtime = SecondaryRuntime(state, record, "unused", interval=10)
        runtime.add("cex_dex", fast)
        runtime.add("spot_spot", normal)
        await runtime.start()
        await asyncio.wait_for(enough.wait(), 2)
        await runtime.stop()
        assert fast.calls >= 3 and normal.calls == 1
    asyncio.run(go())


def test_server_holds_single_instance_lock_before_application(monkeypatch, tmp_path):
    pytest.importorskip("fcntl")  # Linux server entrypoint, not Windows startup.
    import app.paper_server as server
    db = tmp_path / "paper.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE history(value TEXT)")
    env = tmp_path / "bot.env"
    env.write_text("BOT_TOKEN=placeholder\nDEX_VIRTUAL_PAPER_ENABLED=true\nLIVE_ENABLED=false\nLIVE_ENTRY_ENABLED=false\nDEX_LIVE_ENABLED=false\n")
    for key in ("LIVE_ENABLED", "LIVE_ENTRY_ENABLED", "DEX_LIVE_ENABLED"):
        monkeypatch.setenv(key, "false")
    monkeypatch.setenv("PAPER_SERVER_ENV", str(env))
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("BOT_TOKEN", "placeholder")
    monkeypatch.setenv("DEX_VIRTUAL_PAPER_ENABLED", "true")
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        with pytest.raises(ValueError, match="ALREADY_RUNNING"):
            server.main()
    monkeypatch.setattr(server.runpy, "run_module", run)
    server.main()
    assert len(calls) == 1
