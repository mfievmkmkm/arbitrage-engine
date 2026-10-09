import asyncio
import copy
import json
from types import SimpleNamespace as NS
import aiosqlite
import pytest
from app.dex_firm_simulation import Cycle as FirmCycle
from app.cex_dex_paper_source import Source, calendar
from app.cex_dex_paper import Engine, Cycle
from app.cex_dex_replay_lineage import validate
from app.spot_future_history_replay import dataset
from tests.test_dex_firm_simulation import (
    Sim,
    registry,
    raw,
    SELL,
    BUY,
    TAKER,
    HASH,
    NOW,
)
from tests.test_spot_live_units import client, FS


def test_exact_out_checks_bound_and_simulates_maximum_input():
    async def run():
        row = raw()
        row.pop("minBuyAmount")
        row.update(
            sellToken=SELL,
            buyToken=BUY,
            buyAmount="49400000000000000",
            sellAmount="3900000",
            maxSellAmount="4000000",
        )
        p = Sim(row)
        q = await p.firm(
            1, SELL, BUY, "49400000000000000", TAKER, registry(), exact_out=True
        )
        assert q["ok"], q
        assert q["sell_amount_raw"] == q["max_sell_amount_raw"] == "4000000"
        params = p.session.calls[0][1]["params"]
        assert params["buyAmount"] == "49400000000000000" and "sellAmount" not in params
        assert q["min_buy_amount_raw"] == q["buy_amount_raw"] == params["buyAmount"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("maxSellAmount", None),
        ("maxSellAmount", "01"),
        ("maxSellAmount", "3800000"),
        ("buyAmount", "1"),
        ("minBuyAmount", "49400000000000000"),
    ],
)
def test_exact_out_invalid_or_mixed_quotes_fail_before_rpc(field, value):
    async def run():
        row = raw()
        row.pop("minBuyAmount")
        row.update(
            buyAmount="49400000000000000", sellAmount="3900000", maxSellAmount="4000000"
        )
        row[field] = value
        p = Sim(row)
        q = await p.firm(
            1, SELL, BUY, "49400000000000000", TAKER, registry(), exact_out=True
        )
        assert not q["ok"] and not p.calls

    asyncio.run(run())


class Quotes:
    def __init__(self, clock):
        self.clock, self.calls, self.exit_fault = clock, [], False
        self.forward_cash, self.reverse_cash = "4800000", "4800000"

    async def firm(self, chain, sell, buy, amount, taker, reg, **kwargs):
        self.calls.append((sell, buy, str(amount), kwargs))
        is_entry = (sell == SELL and str(amount) == "4000000") or (
            sell == BUY
            and str(amount) == "49400000000000000"
            and not kwargs.get("exact_out")
            and len(self.calls) == 1
        )
        if self.exit_fault and not is_entry:
            return dict(ok=False, reason="DEX_TEST_EXIT_FAILURE")
        exact_out = kwargs.get("exact_out", False)
        sold = self.reverse_cash if exact_out else str(amount)
        bought = (
            str(amount)
            if exact_out
            else (
                "49400000000000000"
                if sell == SELL
                else "5000000" if is_entry else self.forward_cash
            )
        )
        return dict(
            ok=True,
            simulation_verified=True,
            chain_id=chain,
            sell_token=sell,
            buy_token=buy,
            sell_amount_raw=sold,
            buy_amount_raw=bought,
            min_buy_amount_raw=bought,
            max_sell_amount_raw=sold if exact_out else None,
            quote_mode="exact_out" if exact_out else "exact_in",
            network_fee_raw="1000000000000",
            asset=BUY,
            quote_token=SELL,
            block_number=100,
            block_hash=HASH,
            ts=self.clock(),
            received_at=self.clock(),
            quote_fingerprint="a" * 64,
        )


def setup(reverse=False):
    now = [NOW]
    clock = lambda: now[0]
    q = Quotes(clock)
    c = client()
    c.has.update(fetchTradingFee=True, fetchFundingRateHistory=True)
    history = []

    async def fee(symbol):
        return dict(symbol=symbol, taker=0.001)

    async def book(symbol, limit=20):
        px = 100 if symbol == FS else 2000
        return dict(
            symbol=symbol,
            timestamp=clock() * 1000,
            bids=[[px, 1000]],
            asks=[[px + 0.01, 1000]],
        )

    async def rates(symbol, since, limit):
        return copy.deepcopy(history)

    async def snapshot(venue, symbol):
        return NS(next_ts=1010000, interval_hours=8, rate=0.9)

    c.fetch_trading_fee, c.fetch_order_book, c.fetch_funding_rate_history = (
        fee,
        book,
        rates,
    )
    route = dict(
        chain_id=1,
        sell_token=BUY if reverse else SELL,
        buy_token=SELL if reverse else BUY,
        sell_amount_raw="49400000000000000" if reverse else "4000000",
        taker=TAKER,
        label="X",
    )
    cycle = FirmCycle(q, [route], registry(), {"a": c}, {"a": c}, clock=clock)
    source = Source(cycle, NS(get=snapshot), clock=clock)
    return now, q, source, route, history


@pytest.mark.parametrize("reverse", [False, True])
def test_two_sided_roundtrip_raw_inventory_gas_fees_replay_and_restart(
    tmp_path, reverse
):
    async def run():
        now, q, source, route, history = setup(reverse)
        path = tmp_path / "dex.db"
        e = Engine(path, source, clock=lambda: now[0])
        await e.init()
        await e.cycle(route)
        assert len(e.positions) == 1 and e.used_capital == 12
        p = next(iter(e.positions.values()))
        assert p["base_qty"] == 0.049 and p["asset_amount_raw"] == "49400000000000000"
        assert q.calls[-1][2] == p["asset_amount_raw"]
        assert bool(q.calls[-1][3].get("exact_out")) is reverse
        restored = Engine(path, source, clock=lambda: now[0])
        await restored.init()
        assert restored.positions == e.positions and restored.used_capital == 12
        now[0] = 1001
        closed = await restored.cycle(entry_enabled=False)
        assert len(closed) == 1 and closed[0]["status"] == "CLOSED"
        p = closed[0]
        m = p["last_mark"]
        validate(p, m, 1001)
        assert m["funding"] == 0 and m["funding_known"]
        assert m["entry_fees"] == pytest.approx(
            p["base_qty"] * p["entry_price"] * 0.001 + p["entry_gas"]
        )
        assert p["net"] == pytest.approx(
            m["gross"] - m["entry_fees"] - m["exit_fees"] - m["safety"]
        )
        assert restored.used_capital == 0
        await restored.cycle(entry_enabled=False)
        async with aiosqlite.connect(path) as d:
            async with d.execute(
                "SELECT COUNT(*),SUM(amount) FROM ledger WHERE kind='CEX_DEX_PAPER_NET'"
            ) as c:
                count, net = await c.fetchone()
            assert count == 1 and net == p["net"]
            async with d.execute("SELECT payload FROM cex_dex_paper") as c:
                payload = (await c.fetchone())[0]
            assert TAKER not in payload
        trades, excluded = await dataset(path, strategy="cex_dex")
        assert len(trades) == 1 and not excluded, excluded

    asyncio.run(run())


def test_missing_history_freezes_exit_holds_reserve_then_settles_once(tmp_path):
    async def run():
        now, q, source, route, history = setup()
        e = Engine(tmp_path / "dex.db", source, max_seconds=10, clock=lambda: now[0])
        await e.init()
        await e.cycle(route)
        now[0] = 1012
        assert not await e.cycle(entry_enabled=False)
        p = next(iter(e.positions.values()))
        assert p["status"] == "EXIT_ACCOUNTING_PENDING" and e.used_capital == 12
        assert p["exit_at"] == 1012
        calls = len(q.calls)
        now[0] = 1045
        history.append(dict(symbol=FS, timestamp=1010000, fundingRate=0.001))
        closed = await e.cycle(entry_enabled=False)
        assert len(closed) == 1 and len(q.calls) == calls
        assert closed[0]["closed_at"] == 1012
        assert closed[0]["last_mark"]["funding"] == pytest.approx(0.0049)
        trades, excluded = await dataset(e.path, strategy="cex_dex")
        assert len(trades) == 1 and not excluded, excluded

    asyncio.run(run())


def test_exit_failure_does_not_fabricate_flat_or_release_reserve(tmp_path):
    async def run():
        now, q, source, route, _ = setup()
        e = Engine(tmp_path / "dex.db", source, clock=lambda: now[0])
        await e.init()
        await e.cycle(route)
        q.exit_fault = True
        now[0] = 1001
        assert not await e.cycle(entry_enabled=False)
        p = next(iter(e.positions.values()))
        assert (
            p["status"] == "OPEN"
            and p["data_reason"] == "DEX_TEST_EXIT_FAILURE"
            and e.used_capital == 12
        )

    asyncio.run(run())


def test_pending_reservation_visible_and_cancel_releases_it(tmp_path):
    async def run():
        now, q, source, route, _ = setup()
        e = Engine(tmp_path / "dex.db", source, clock=lambda: now[0])
        await e.init()
        entered, release = asyncio.Event(), asyncio.Event()
        original = source.entry

        async def slow(r):
            entered.set()
            await release.wait()
            return await original(r)

        source.entry = slow
        task = asyncio.create_task(e.cycle(route))
        await entered.wait()
        assert e.used_capital == 12
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert e.used_capital == 0 and not e.positions

    asyncio.run(run())


def test_shared_budget_rechecks_after_network_io(tmp_path):
    async def run():
        now, q, source, route, _ = setup()
        e = Engine(tmp_path / "dex.db", source, clock=lambda: now[0])
        await e.init()
        reserve = [0]
        e.external_reserved = lambda: reserve[0]
        original = source.entry

        async def racing(r):
            result = await original(r)
            reserve[0] = 45
            return result

        source.entry = racing
        await e.cycle(route)
        assert not e.positions and not e.pending

    asyncio.run(run())


def test_concurrent_writer_cannot_duplicate_position_or_ledger(tmp_path):
    async def run():
        now, q, source, route, _ = setup()
        path = tmp_path / "dex.db"
        a, b = Engine(path, source, clock=lambda: now[0]), Engine(
            path, source, clock=lambda: now[0]
        )
        await a.init()
        await b.init()
        await a.cycle(route)
        with pytest.raises(ValueError, match="CONCURRENT_WRITER"):
            await b.cycle(route)
        assert not b.pending and not b.positions

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["missing", "conflict", "calendar", "truncated", "maturity"]
)
def test_single_side_funding_requires_complete_mature_history(fault):
    async def run():
        now, q, source, route, history = setup()
        x = await source.entry(route)
        p = x["position"]
        now[0] = 1045 if fault != "maturity" else 1012
        history.append(dict(symbol=FS, timestamp=1010000, fundingRate=0.001))
        if fault == "missing":
            history.clear()
        if fault == "conflict":
            history.append(dict(symbol=FS, timestamp=1010000, fundingRate=0.002))
        if fault == "calendar":
            history[0]["timestamp"] = 1015000
        if fault == "truncated":
            history.extend([history[0]] * 99)
        h = await source.settlements(p, now[0])
        assert not h.verified and h.amount == 0

    asyncio.run(run())


def test_settlement_during_quote_is_not_covered_by_earlier_history():
    async def run():
        now, q, source, route, _ = setup()
        p = (await source.entry(route))["position"]
        h = await source.settlements(p, 1009)
        assert h.verified
        h = source.extend_history(p, h, 1011)
        assert not h.verified

    asyncio.run(run())


def test_entry_refreshes_cex_books_after_slow_reverse_rpc():
    async def run():
        now, q, source, route, _ = setup()
        original = q.firm

        async def slow(*args, **kwargs):
            if len(q.calls) == 1:
                now[0] += 3
            return await original(*args, **kwargs)

        q.firm = slow
        x = await source.entry(route)
        assert x["ok"], x
        p = x["position"]
        assert p["opened_at"] == 1003 and p["entry_cex"]["book_ts"] == 1003
        assert p["entry_dex"]["ts"] == 1000

    asyncio.run(run())


@pytest.mark.parametrize(
    "reverse,cash,reason",
    [
        (True, "6000000", "MICRO_LIMIT"),
        (False, "3900000", "NET_STOP"),
    ],
)
def test_entry_rejects_unaffordable_or_already_stopped_reverse_route(
    reverse, cash, reason
):
    async def run():
        _, q, source, route, _ = setup(reverse)
        if reverse:
            q.reverse_cash = cash
        else:
            q.forward_cash = cash
        x = await source.entry(route)
        assert not x["ok"] and reason in x["reason"], x

    asyncio.run(run())


def test_long_funding_cashflow_is_negative_for_positive_settlement_rate():
    async def run():
        now, _, source, route, history = setup(True)
        p = (await source.entry(route))["position"]
        now[0] = 1045
        history.append(dict(symbol=FS, timestamp=1010000, fundingRate=0.001))
        h = await source.settlements(p, 1045)
        assert h.verified and h.amount == pytest.approx(-0.00490049)

    asyncio.run(run())


def test_gas_identity_change_cannot_revalue_existing_position():
    async def run():
        _, _, source, route, _ = setup()
        p = (await source.entry(route))["position"]
        source.cycle.registry["chains"]["1"]["native_decimals"] = 6
        x = await source.exit(p)
        assert not x["ok"] and x["reason"] == "DEX_NATIVE_GAS_IDENTITY_CHANGED"

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["raw", "gas", "fees", "entry", "funding", "stale", "price"]
)
def test_replay_rejects_tampered_cost_quantity_and_chain_lineage(tmp_path, fault):
    async def run():
        now, q, source, route, _ = setup()
        e = Engine(tmp_path / "dex.db", source, clock=lambda: now[0])
        await e.init()
        await e.cycle(route)
        now[0] = 1001
        p = (await e.cycle(entry_enabled=False))[0]
        m = copy.deepcopy(p["last_mark"])
        if fault == "raw":
            m["exit_dex"]["sell_amount_raw"] = "1"
        if fault == "gas":
            m["exit_gas"] += 0.1
        if fault == "fees":
            m["entry_fees"] = 0
        if fault == "entry":
            m["entry_dex"]["quote_fingerprint"] = "b" * 64
        if fault == "funding":
            m["funding"] = 1
        if fault == "stale":
            m["exit_dex"]["ts"] = 900
        if fault == "price":
            m["exit_price"] += 1
        with pytest.raises(ValueError):
            validate(p, m, 1001)

    asyncio.run(run())
