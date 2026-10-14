import asyncio, json, sqlite3, time, copy
import pytest
from app.db import Diary
from app.paper import PaperEngine
from app.replay import simulate as legacy_sim, ReplayParams, portfolio_replay
from app.spot_future_history_replay import dataset, evaluate, simulate, render, build
from app.reports import build_replay_report


def quote(ts):
    return dict(
        symbol="X/USDT:USDT",
        buy="binance",
        sell="bybit",
        notional=100,
        base_qty=1,
        entry_buy=100,
        entry_sell=110,
        executable=10,
        fee_pct=0.4,
        safety_pct=0.1,
        exit_buy=99,
        exit_sell=111,
        exit_spread=12,
        ts=ts,
    )


async def history(path, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    d = Diary(path)
    await d.init()
    e = PaperEngine(d, 500, target=0.5, max_seconds=120)
    p = await e.open(quote(1000))
    assert p.last_mark and p.current_net_usd < 0
    clock[0] = 1060
    await e.mark_and_exit(
        [dict(quote(1060), exit_buy=103, exit_sell=108, exit_spread=8)]
    )
    clock[0] = 1120
    assert await e.mark_and_exit(
        [dict(quote(1120), exit_buy=104, exit_sell=107, exit_spread=6)]
    )
    return d, p


def test_primary_has_atomic_entry_mark_and_verified_replay(tmp_path, monkeypatch):
    async def go():
        d, p = await history(str(tmp_path / "d.db"), monkeypatch)
        rows, excluded = await dataset(d.path, strategy="futures_futures")
        assert len(rows) == 1 and not excluded and len(rows[0]["marks"]) == 3
        assert simulate(rows, 120, 0.2, 0.5)["metrics"]["net"] == pytest.approx(
            p.current_net_usd
        )
        report = await build(d.path, strategy="futures_futures")
        assert not report["release_authorized"]
        assert "Фьючерсы ↔ Фьючерсы" in render(report)
        _, new = await build_replay_report(d)
        assert new == report

    asyncio.run(go())


@pytest.mark.parametrize(
    "case",
    ["legacy", "qty", "entry_fees", "safety", "spread", "net", "negative", "gap"],
)
def test_primary_rejects_inconsistent_or_unproven_marks(tmp_path, monkeypatch, case):
    async def go():
        d, _ = await history(str(tmp_path / "d.db"), monkeypatch)
        with sqlite3.connect(d.path) as c:
            if case == "legacy":
                c.execute("UPDATE paper_marks SET payload=NULL WHERE id=1")
            elif case == "gap":
                c.execute("UPDATE paper_marks SET ts=1300 WHERE id=2")
            else:
                p = json.loads(
                    c.execute("SELECT payload FROM paper_marks WHERE id=2").fetchone()[
                        0
                    ]
                )
                if case == "qty":
                    p["base_qty"] = 2
                if case == "entry_fees":
                    p["entry_fees"] = 0.99
                    p["net"] -= 0.78
                    c.execute(
                        "UPDATE paper_marks SET net_usd=? WHERE id=2", (p["net"],)
                    )
                if case == "safety":
                    p["safety"] = 0.5
                    p["net"] -= 0.4
                    c.execute(
                        "UPDATE paper_marks SET net_usd=? WHERE id=2", (p["net"],)
                    )
                if case == "spread":
                    p["exit_spread"] = 9
                if case == "net":
                    p["net"] = 999
                if case == "negative":
                    p["exit_fees"] = -0.1
                c.execute(
                    "UPDATE paper_marks SET payload=? WHERE id=2", (json.dumps(p),)
                )
        rows, excluded = await dataset(d.path, strategy="futures_futures")
        assert not rows and sum(excluded.values()) == 1

    asyncio.run(go())


def primary_trades():
    return [
        dict(
            id=i,
            opened_at=i * 1000 + 1,
            closed_at=i * 1000 + 601,
            entry_spread=10,
            marks=[
                (i * 1000 + 1, -0.2, 10),
                (i * 1000 + 121, 1, 4),
                (i * 1000 + 301, 0.5, 5),
                (i * 1000 + 601, 0.1, 6),
            ],
        )
        for i in range(10)
    ]


def test_primary_purges_overlap_and_never_selects_parameters_on_test():
    xs = primary_trades()
    original = evaluate(xs, strategy="futures_futures")
    assert (
        original["status"] == "VALIDATED_SPLIT" and "target" in original["parameters"]
    )
    for x in xs[7:]:
        x["marks"] = [(ts, -99, spread) for ts, _, spread in x["marks"]]
    changed = evaluate(xs, strategy="futures_futures")
    assert (
        changed["parameters"] == original["parameters"]
        and not changed["model_positive"]
    )
    xs[6]["closed_at"] = xs[7]["opened_at"]
    assert evaluate(xs, strategy="futures_futures")["purged"] == 1
    for x in xs[7:]:
        x["marks"] = [(x["opened_at"], 1, 9)]
    incomplete = evaluate(xs, strategy="futures_futures")
    assert incomplete["status"] == "CENSORED_TEST" and not incomplete["test"]["metrics"]
    assert legacy_sim(10, [(1, 9, 9)], ReplayParams(0.5, 0.2, 120))["net"] is None
    assert not portfolio_replay([(10, [(1, 9, 9)])])


def test_entry_mark_failure_rolls_back_position_and_reserve(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr(time, "time", lambda: 1000)
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        e = PaperEngine(d, 500)
        with sqlite3.connect(d.path) as c:
            c.execute(
                "CREATE TRIGGER fail BEFORE INSERT ON paper_marks BEGIN SELECT RAISE(ABORT,'entry mark failed'); END"
            )
        with pytest.raises(Exception, match="entry mark failed"):
            await e.open(quote(1000))
        assert not e.positions and e.used_capital == 0
        with sqlite3.connect(d.path) as c:
            assert c.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0

    asyncio.run(go())


def test_restart_preserves_latest_mark_and_rejects_backwards_observation(
    tmp_path, monkeypatch
):
    async def go():
        clock = [1000]
        monkeypatch.setattr(time, "time", lambda: clock[0])
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        e = PaperEngine(d, 500, target=99, max_seconds=999)
        await e.open(quote(1000))
        clock[0] = 1060
        await e.mark_and_exit([quote(1060)])
        restarted = PaperEngine(d, 500, target=99, max_seconds=999)
        await restarted.restore()
        before = copy.deepcopy(restarted.positions[1])
        clock[0] = 1061
        await restarted.mark_and_exit([dict(quote(1059), exit_buy=999)])
        assert restarted.positions[1] == before and before.last_mark["ts"] == 1060

    asyncio.run(go())


def test_legacy_windowless_replay_cannot_claim_validated_split():
    from app.replay import walk_forward
    from app.reports import compact_ai_json

    result = walk_forward([(10, [(120, 9, 1)])] * 10)
    assert (
        result["status"] == "window_metadata_required"
        and not result["release_authorized"]
    )
    assert (
        json.loads(compact_ai_json({"profit_factor": float("inf")}))["profit_factor"]
        is None
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0])
def test_invalid_entry_exit_price_never_creates_position(tmp_path, bad):
    e = PaperEngine(Diary(str(tmp_path / "d.db")), 500)
    assert not e.can_open(dict(quote(1000), exit_buy=bad))


def test_drawdown_follows_exit_time_instead_of_entry_order():
    xs = [
        dict(id=1, opened_at=0, marks=[(50, -5)]),
        dict(id=2, opened_at=10, marks=[(100, -5)]),
        dict(id=3, opened_at=20, marks=[(75, 10)]),
    ]
    m = simulate(xs, 10, 0.2)["metrics"]
    assert m["net"] == 0 and m["max_drawdown"] == 5 and m["trades"] == 3
    # Concurrent realization is one equity change, independent of ID ordering.
    xs[0]["marks"] = [(50, 10)]
    xs[1]["marks"] = [(50, -10)]
    xs = xs[:2]
    assert simulate(xs, 10, 0.2)["metrics"]["max_drawdown"] == 0
