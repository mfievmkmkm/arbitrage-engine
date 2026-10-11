import asyncio
import sqlite3
from decimal import Decimal, ROUND_DOWN
from types import SimpleNamespace

import pytest

from app.cex_dex_paper import Engine
from app.cex_dex_paper_view import render
from app.dex_virtual_paper_source import Source, routes_checked

STABLE = "0x" + "1" * 40
ASSET = "0x" + "2" * 40
ROUTE = dict(chain_id=1, stable=STABLE, asset=ASSET, stable_decimals=6,
             asset_decimals=2, base="X", cex_venue="bybit", cex_symbol="X/USDT:USDT",
             native_symbol="ETH/USDT:USDT", native_decimals=18, notional_usd=4,
             label="X virtual", mode="paired")


class Public:
    precisionMode = 4
    has = {"fetchFundingRateHistory": True}

    def __init__(self, clock):
        self.clock, self.bid = clock, 1.04

    def market(self, symbol):
        return dict(symbol=symbol, base="X" if symbol.startswith("X/") else "ETH",
                    contract=True, linear=True, inverse=False, settle="USDT", quote="USDT",
                    active=True, contractSize=1, precision=dict(amount=.01, price=.0001),
                    limits=dict(amount=dict(min=.01), cost=dict(min=.1)), taker=.001)

    def amount_to_precision(self, symbol, amount):
        return str(Decimal(str(amount)).quantize(Decimal(".01"), rounding=ROUND_DOWN))

    async def fetch_order_book(self, symbol, limit=20):
        bid = self.bid if symbol.startswith("X/") else 1000
        return dict(symbol=symbol, timestamp=self.clock() * 1000,
                    bids=[[bid, 100]], asks=[[bid + .001, 100]])

    async def create_order(self, *args, **kwargs):
        raise AssertionError("virtual source must never submit an order")


class Prices:
    def __init__(self, clock):
        self.clock, self.calls, self.missing_gas = clock, [], False

    async def price(self, chain, sell, buy, amount):
        self.calls.append((chain, sell, buy, amount))
        sold = int(amount)
        bought = int(sold / 10000) if sell == STABLE else sold * 10000
        return dict(ok=True, chain_id=chain, sell_token=sell, buy_token=buy,
                    sell_amount_raw=amount, buy_amount_raw=str(bought), ts=self.clock(),
                    network_fee_raw=None if self.missing_gas else "1000000000000",
                    token_metadata={k: dict(buyTaxBps="0", sellTaxBps="0", transferTaxBps="0") for k in ("buyToken", "sellToken")})


def source(mode="paired"):
    now = [100.0]
    clock = lambda: now[0]
    public, provider = Public(clock), Prices(clock)
    route = dict(ROUTE, mode=mode)

    class Funding:
        async def get(self, venue, symbol):
            return SimpleNamespace(next_ts=1000000, interval_hours=8)

    return Source(provider, [route], {"bybit": public}, Funding(), clock=clock), route, public, provider, now


@pytest.mark.parametrize("mode,bid,side", [("paired", 1.04, "sell"), ("reference", 1.04, "sell"), ("reference", .96, "buy")])
def test_virtual_entry_without_wallet_and_both_reference_directions(mode, bid, side):
    async def go():
        s, route, public, provider, now = source(mode)
        public.bid = bid
        result = await s.entry(route)
        assert result["ok"], result
        p = result["position"]
        assert p["cex_side"] == side
        assert p["entry_edge"] > .05
        assert p["live_allowed"] is False and p["execution_verified"] is False
        assert p["model"] == "DEX_VIRTUAL_INDICATIVE_MODEL"
        assert p["virtual_asset_qty"] > 0 if mode == "paired" else p["virtual_asset_qty"] == 0
        if mode == "reference":
            assert p["entry_gas"] == 0
        assert provider.calls
    asyncio.run(go())


def test_paired_roundtrip_restore_close_separate_evidence_and_reservations(tmp_path):
    async def go():
        s, route, public, _, now = source()
        path = tmp_path / "paper.sqlite3"
        firm = Engine(path, s, clock=s.clock)
        await firm.init()
        virtual = Engine(path, s, clock=s.clock, virtual=True)
        await virtual.init()
        await virtual.cycle(route)
        assert len(virtual.positions) == 1
        assert virtual.used_capital == 12
        reopened = Engine(path, s, clock=s.clock, virtual=True)
        await reopened.init()
        assert reopened.positions == virtual.positions
        now[0] += 10
        public.bid = 1.002
        closed = await reopened.cycle(route, entry_enabled=False)
        assert len(closed) == 1 and closed[0]["net"] > 0
        assert reopened.used_capital == 0
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM cex_dex_paper").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM cex_dex_virtual_paper WHERE status='CLOSED'").fetchone()[0] == 1
            assert db.execute("SELECT kind FROM ledger").fetchone()[0] == "CEX_DEX_VIRTUAL_PAPER_NET"
    asyncio.run(go())


def test_shared_budget_blocks_virtual_entry(tmp_path):
    async def go():
        s, route, *_ = source()
        e = Engine(tmp_path / "budget.db", s, capital=20, clock=s.clock, virtual=True)
        await e.init()
        e.external_reserved = lambda: 10
        await e.cycle(route)
        assert not e.positions
        assert e.state["last_decision"] == "SHARED_CAPITAL_LOW"
    asyncio.run(go())


def test_missing_gas_blocks_pair_but_reference_has_no_dex_transaction():
    async def go():
        for mode in ("paired", "reference"):
            s, route, _, provider, _ = source(mode)
            provider.missing_gas = True
            x = await s.entry(route)
            assert x["ok"] is (mode == "reference"), x
    asyncio.run(go())


def test_route_change_holds_position_and_missing_data_does_not_close(tmp_path):
    async def go():
        s, route, *_ = source()
        e = Engine(tmp_path / "hold.db", s, clock=s.clock, virtual=True)
        await e.init()
        await e.cycle(route)
        s.routes[0]["asset_decimals"] = 3
        await e.cycle(entry_enabled=False)
        p = next(iter(e.positions.values()))
        assert p["data_reason"] == "DEX_VIRTUAL_IDENTITY_CHANGED"
        assert e.used_capital == 12
    asyncio.run(go())


def test_reference_net_is_cex_movement_without_dex_leg():
    async def go():
        s, route, public, _, now = source("reference")
        p = (await s.entry(route))["position"]
        public.bid = 1.002
        now[0] += 1
        x = await s.exit(p)
        h = await s.settlements(p, x["ts"])
        m = s.mark(p, x, h)
        assert m["gross"] == pytest.approx(p["base_qty"] * (p["entry_price"] - x["price"]))
        assert m["exit_gas"] == 0 and m["entry_gas"] == 0
        assert m["net"] < m["gross"]
    asyncio.run(go())


def test_configuration_and_ui_distinguish_virtual_from_firm():
    with pytest.raises(ValueError, match="DUPLICATE"):
        routes_checked([ROUTE, dict(ROUTE)])
    with pytest.raises(ValueError, match="MICRO_LIMIT"):
        routes_checked([dict(ROUTE, notional_usd=100)])
    e = SimpleNamespace(virtual=True, positions={}, used_capital=0, state={}, budget=lambda: 50, external_reserved=lambda: 0)
    assert "Виртуальный капитал" in render(e)
    assert "не подтверждение исполнимости" in render(e)


def test_reference_can_exit_when_dex_anchor_disappears(tmp_path):
    async def go():
        s, route, public, provider, now = source("reference")
        e = Engine(tmp_path / "reference.db", s, clock=s.clock, virtual=True)
        await e.init()
        await e.cycle(route)
        assert e.positions
        async def unavailable(*args):
            return dict(ok=False, reason="DEX_HTTP_503")
        provider.price = unavailable
        now[0] += 10
        public.bid = 1.002
        closed = await e.cycle(entry_enabled=False)
        assert len(closed) == 1 and closed[0]["net"] > 0
        assert closed[0]["last_mark"]["exit_dex"]["ok"] is False
    asyncio.run(go())


def test_reference_sizing_caps_premium_notional_at_five_dollars():
    async def go():
        s, route, public, provider, _ = source("reference")
        route["notional_usd"] = 5
        public.bid = 1.05
        x = await s.entry(route)
        assert x["ok"], x
        p = x["position"]
        assert p["base_qty"] * p["entry_price"] <= 5
        assert p["entry_edge"] > .05
    asyncio.run(go())


def test_setup_preserves_credentials_history_and_removes_duplicate_flags(tmp_path):
    from app.configure_virtual_paper import configure, default_routes
    path = tmp_path / ".env"
    original = "BYBIT_API_KEY=placeholder\nZEROX_API_KEY=placeholder\nDB_PATH=existing.db\nPAPER_CAPITAL_USD=40\nLIVE_ENABLED=true\nLIVE_ENABLED=true\n"
    path.write_text(original)
    backup = configure(path, default_routes("reference"))
    assert backup.read_text() == original
    text = path.read_text()
    assert "BYBIT_API_KEY=placeholder" in text and "DB_PATH=existing.db" in text
    assert "PAPER_CAPITAL_USD=40" in text
    assert text.count("LIVE_ENABLED=false\n") == 2  # root and DEX, one each
    assert "LIVE_ENABLED=true" not in text
    assert "DEX_VIRTUAL_PAPER_ENABLED=true" in text


def test_virtual_close_restores_general_paper_equity_once(tmp_path):
    from app.paper_ledger import restore
    async def go():
        s, route, public, _, now = source()
        path = tmp_path / "restore.db"
        e = Engine(path, s, clock=s.clock, virtual=True)
        await e.init()
        await e.cycle(route)
        now[0] += 10
        public.bid = 1.002
        closed = await e.cycle(entry_enabled=False)
        with sqlite3.connect(path) as d:
            d.execute("CREATE TABLE paper_positions(status TEXT,current_net_usd REAL,closed_at REAL)")
        ledger = SimpleNamespace(realized=0)
        await restore(path, ledger)
        assert ledger.realized == pytest.approx(closed[0]["net"])
        await restore(path, ledger)
        assert ledger.realized == pytest.approx(closed[0]["net"])
    asyncio.run(go())


@pytest.mark.parametrize("flag", ["LIVE_ENABLED", "DEX_LIVE_ENABLED", "options"])
def test_virtual_bootstrap_rejects_live_before_constructing_clients(monkeypatch, flag):
    from app.secondary_bootstrap import build_bundle
    monkeypatch.setenv("DEX_VIRTUAL_PAPER_ENABLED", " true ")
    monkeypatch.setenv("LIVE_ENABLED", "false")
    monkeypatch.setenv("DEX_LIVE_ENABLED", "false")
    options = {"live_enabled": flag == "options"}
    if flag != "options":
        monkeypatch.setenv(flag, " TRUE ")
    with pytest.raises(ValueError, match="VIRTUAL_RUN_REQUIRES_LIVE_DISABLED"):
        asyncio.run(build_bundle([], 5, 1, 30, None, None, "unused.db", dex_options=options))
