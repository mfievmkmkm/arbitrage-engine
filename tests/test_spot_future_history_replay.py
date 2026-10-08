import asyncio
import json
import sqlite3
from dataclasses import replace
import pytest
from app.spot_future_paper import open_from, mark
from app.spot_future_paper_engine import Engine
from app.spot_future_source import SpotFutureSource
from app.spot_future_history_replay import dataset, evaluate, simulate, build
from app import spot_future_paper_store as storage
from app.strategy_paper_coordinator import Coordinator


def op(ts=1000, qty=1):
    return dict(
        base="X",
        exchange="a",
        direction="LONG_SPOT_SHORT_FUTURE",
        notional=100,
        base_qty=qty,
        prices=dict(spot_buy=100, spot_sell=99.9, future_buy=110.1, future_sell=110),
        fee_pct=0.4,
        safety_pct=0.1,
        funding_pct=5,
        ts=ts,
        hypothetical_edge=8,
    )


def test_cost_lineage_is_frozen_and_forecast_funding_is_not_credited():
    x = op()
    p = open_from(x)
    assert p.last_mark and p.last_mark["funding"] == 0
    x.update(fee_pct=0.8, safety_pct=99)
    x["prices"] = dict(spot_buy=105.1, spot_sell=105, future_buy=106, future_sell=105.9)
    net = mark(p, x)
    assert p.last_mark["entry_fees"] == pytest.approx(0.21)
    assert p.last_mark["exit_fees"] == pytest.approx(0.422)
    assert p.last_mark["safety"] == 0.1
    assert net == pytest.approx(9 - 0.21 - 0.422 - 0.1)
    x["base_qty"] = 0.9
    with pytest.raises(ValueError, match="QUANTITY_MISMATCH"):
        mark(p, x)


def test_closed_route_is_not_reopened_in_same_cycle():
    e = Engine(capital=500, max_age=10)
    e.open(op())
    opened, closed = Coordinator(e).spot_future([op(1020)], 1)
    assert len(closed) == 1 and not opened and not e.positions


def test_watch_quotes_use_saved_position_quantity_even_after_price_change():
    class C:
        async def load_markets(self):
            return {
                "s": dict(symbol="X/USDT", base="X", quote="USDT", spot=True),
                "f": dict(
                    symbol="X/USDT:USDT",
                    base="X",
                    quote="USDT",
                    settle="USDT",
                    linear=True,
                    swap=True,
                    contractSize=0.1,
                ),
            }

        async def fetch_order_book(self, symbol):
            return dict(asks=[[200, 10]], bids=[[199, 10]])

    async def go():
        source = SpotFutureSource({"a": C()}, 10)
        await source.load()
        p = open_from(op(qty=0.4))
        source.watch_pairs = {("a", "X")}
        source.watch_positions = {("a", "X"): p}
        source.allowed_venue = lambda v: False
        rows = await source.scan()
        assert rows[0]["base_qty"] == 0.4

    asyncio.run(go())


async def history(path):
    await storage.init(path)
    p = open_from(op(1000))
    await storage.save(path, p)
    await storage.mark(path, p, 1000)
    for ts in (1060, 1120):
        x = op(ts)
        x["prices"].update(spot_sell=102, future_buy=106)
        mark(p, x)
        await storage.mark(path, p, ts)
    p.status = "TIME_STOP"
    await storage.save(path, p)
    with sqlite3.connect(path) as d:
        d.execute("UPDATE spot_future_paper SET closed_at=1120")


def test_durable_marks_preserve_costs_and_replay_lineage(tmp_path):
    async def go():
        path = str(tmp_path / "d.db")
        await history(path)
        rows, excluded = await dataset(path)
        assert len(rows) == 1 and not excluded
        assert simulate(rows, 120, 0.2)["metrics"]["trades"] == 1
        report = await build(path)
        assert (
            report["status"] == "INSUFFICIENT_DATA" and not report["release_authorized"]
        )

    asyncio.run(go())


@pytest.mark.parametrize(
    "case", ["legacy", "gap", "quantity", "cost", "nan", "duplicate"]
)
def test_bad_history_is_excluded_instead_of_inventing_net(tmp_path, case):
    async def go():
        path = str(tmp_path / "d.db")
        await history(path)
        with sqlite3.connect(path) as d:
            if case == "legacy":
                d.execute("UPDATE spot_future_marks SET payload=NULL")
            elif case == "gap":
                d.execute("UPDATE spot_future_marks SET ts=1500 WHERE id=3")
            else:
                payload = json.loads(
                    d.execute(
                        "SELECT payload FROM spot_future_marks WHERE id=2"
                    ).fetchone()[0]
                )
                if case == "quantity":
                    payload["base_qty"] = 2
                if case == "cost":
                    payload["gross"] = 999
                if case == "nan":
                    payload["funding"] = float("nan")
                if case == "duplicate":
                    d.execute("UPDATE spot_future_marks SET ts=1060,net=99 WHERE id=3")
                    payload["net"] = 99
                d.execute(
                    "UPDATE spot_future_marks SET payload=? WHERE id=2",
                    (json.dumps(payload),),
                )
        rows, excluded = await dataset(path)
        assert not rows and sum(excluded.values()) == 1

    asyncio.run(go())


def trades(n=10):
    return [
        dict(
            id=i,
            opened_at=i * 1000 + 1,
            closed_at=i * 1000 + 601,
            marks=[
                (i * 1000 + 1, -0.2),
                (i * 1000 + 121, 1),
                (i * 1000 + 301, 0.5),
                (i * 1000 + 601, 0.1),
            ],
        )
        for i in range(n)
    ]


def test_parameters_are_selected_only_on_train_and_overlap_is_purged():
    data = trades()
    first = evaluate(data)
    assert first["status"] == "VALIDATED_SPLIT"
    original = first["parameters"]
    for x in data[7:]:
        x["marks"] = [(ts, -99) for ts, _ in x["marks"]]
    changed = evaluate(data)
    assert changed["parameters"] == original and not changed["model_positive"]
    data[6]["closed_at"] = data[7]["opened_at"]
    purged = evaluate(data)
    assert purged["purged"] == 1 and purged["train_size"] == 6
    assert not purged["release_authorized"]


def test_censored_paths_are_reported_without_forced_last_price_exit():
    data = trades()
    for x in data[7:]:
        x["marks"] = [(x["opened_at"], 0.1)]
    result = evaluate(data)
    assert result["status"] == "CENSORED_TEST" and result["test"]["censored"] == 3
    assert result["test"]["metrics"] is None and not result["model_positive"]


def test_capital_reserves_actual_values_of_both_legs():
    x = op()
    x.update(notional=100, base_qty=1)
    engine = Engine(capital=205)
    assert not engine.open(x)
    engine.capital = 215
    assert engine.open(x)
    assert engine.used_capital == 210
