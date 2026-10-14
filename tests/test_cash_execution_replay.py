import asyncio
import copy
from functools import wraps
import json
import sqlite3
import zipfile
from dataclasses import asdict

import pytest

from app.cash_execution_replay import build, normalize, render, simulate
from app.execution_book_replay import Scenario, Tape
from app.secondary_book_history import spec
from tests.test_secondary_book_history import market

BASE = Scenario("BASE", 0, 0)


def offline_async(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return run


def position(strategy):
    p = dict(
        id=1,
        opened_at=100,
        closed_at=110,
        status="TARGET",
        base_qty=1,
        entry_fee_pct=0.2,
    )
    if strategy == "spot_futures":
        p.update(
            base="X",
            exchange="a",
            direction="LONG_SPOT_SHORT_FUTURE",
            notional=100,
            safety_pct=0.1,
        )
    else:
        p.update(
            symbol="X/USDT",
            buy="a",
            sell="b",
            entry_buy=100,
            safety=0.1,
            inventory_evidence=dict(
                mode="PREFUNDED_PAPER_INVENTORY",
                buy="a",
                sell="b",
                asset="X",
                buy_quote=200,
                sell_quote=10,
                sell_base=1,
            ),
        )
    return p


def book(venue, symbol, ts, bid, ask, depth=10):
    return dict(
        mode="PUBLIC_REST_BASE_UNITS",
        venue=venue,
        symbol=symbol,
        book_ts=ts,
        received_at=ts,
        bids=[[bid, depth]],
        asks=[[ask, depth]],
        instrument=asdict(spec(venue, market(symbol))),
    )


def books(strategy, first_depth=10, second_depth=10):
    second_venue, second_symbol = (
        ("a", "X/USDT:USDT") if strategy == "spot_futures" else ("b", "X/USDT")
    )
    return [
        book("a", "X/USDT", 100, 99, 100, first_depth),
        book(second_venue, second_symbol, 100, 110, 111, second_depth),
        book("a", "X/USDT", 110, 104, 105),
        book(second_venue, second_symbol, 110, 106, 107),
    ]


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_sequential_roundtrip_net_and_distinct_instruments(strategy):
    r = simulate(strategy, position(strategy), Tape(books(strategy)), BASE)
    assert r["status"] == "CLOSED"
    assert r["residual"] == [0, 0]
    assert r["cashflow"] == 7
    assert r["fees"] == pytest.approx((100 + 110 + 107 + 104) * 0.0005)
    assert r["net"] == pytest.approx(7 - r["fees"] - 0.1)
    assert [(o["leg"], o["side"]) for o in r["orders"]] == [
        (0, "BUY"),
        (1, "SELL"),
        (1, "BUY"),
        (0, "SELL"),
    ]
    assert not r["release_authorized"] and r["funding"] == 0


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_partial_first_leg_caps_hedge_and_aborts_flat(strategy):
    r = simulate(
        strategy, position(strategy), Tape(books(strategy, first_depth=0.4)), BASE
    )
    assert r["orders"][1]["requested"] == pytest.approx(0.4)
    assert r["status"] == "ENTRY_ABORT_FLAT"
    assert r["residual"] == pytest.approx([0, 0])
    assert r["net"] < 0 and r["fees"] > 0


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_partial_second_leg_is_unwound_and_all_fills_charged(strategy):
    r = simulate(
        strategy, position(strategy), Tape(books(strategy, second_depth=0.3)), BASE
    )
    assert r["status"] == "ENTRY_ABORT_FLAT"
    assert r["orders"][2]["requested"] == pytest.approx(0.3)
    assert r["orders"][3]["requested"] == pytest.approx(1)
    assert r["fees"] == pytest.approx(
        sum(o["filled"] * (o["price"] or 0) * 0.0005 for o in r["orders"])
    )


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_recovery_does_not_replenish_same_snapshot_liquidity(strategy):
    data = books(strategy)
    data[-1]["asks"] = [[107, 0.4]]
    r = simulate(strategy, position(strategy), Tape(data), BASE)
    assert r["status"] == "RESIDUAL_EXPOSURE" and r["net"] is None
    assert r["residual"] == pytest.approx([0.6, -0.6])
    assert sum(
        o["filled"] for o in r["orders"] if o["leg"] == 1 and o["side"] == "BUY"
    ) == pytest.approx(0.4)
    assert len([o for o in r["orders"] if o["phase"].startswith("RECOVERY")]) <= 6


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_fresh_snapshot_allows_bounded_recovery(strategy):
    data = books(strategy)
    data[-1]["asks"] = [[107, 0.4]]
    fresh = copy.deepcopy(data[-1])
    fresh.update(book_ts=110.5, received_at=110.5, asks=[[108, 1]])
    r = simulate(strategy, position(strategy), Tape(data + [fresh]), BASE)
    assert r["status"] == "CLOSED_WITH_RECOVERY" and r["net"] is not None
    assert r["residual"] == pytest.approx([0, 0])
    assert any(o["price"] == 108 for o in r["orders"])


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_missing_exit_never_becomes_profit(strategy):
    r = simulate(strategy, position(strategy), Tape(books(strategy)[:2]), BASE)
    assert r["status"] == "RESIDUAL_EXPOSURE" and r["net"] is None
    assert r["residual"] == [1, -1]
    assert all(o["filled"] == 0 for o in r["orders"] if o["phase"] != "ENTRY")


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_latency_observes_only_received_history(strategy):
    data = books(strategy)
    changed = copy.deepcopy(data[1])
    changed.update(book_ts=100.5, received_at=100.5, bids=[[108, 10]])
    tape = Tape(data + [changed])
    fast = simulate(strategy, position(strategy), tape, BASE)
    slow = simulate(strategy, position(strategy), tape, Scenario("LAG", 0, 0.5))
    assert fast["orders"][1]["price"] == 110
    assert slow["orders"][1]["price"] == 108
    assert slow["net"] < fast["net"]


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_late_hedge_is_not_opened_after_original_exit(strategy):
    p = position(strategy)
    p["closed_at"] = 100.1
    r = simulate(strategy, p, Tape(books(strategy)), Scenario("LATE", 0, 0.5))
    assert r["status"] == "LATE_ENTRY_ABORT_FLAT"
    assert not any(o["leg"] == 1 for o in r["orders"])


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
def test_instrument_change_holds_unclosed_exposure(strategy):
    data = books(strategy)
    data[-1]["instrument"]["contract_size"] = 0.2 if strategy == "spot_futures" else 1
    if strategy == "spot_spot":
        data[-1]["instrument"]["base"] = "Y"
    r = simulate(strategy, position(strategy), Tape(data), BASE)
    assert r["status"] == "RESIDUAL_EXPOSURE"
    assert any(
        o["reason"] in ("INSTRUMENT_CHANGED", "INSTRUMENT_MISMATCH")
        for o in r["orders"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"base_qty": True},
        {"base_qty": float("nan")},
        {"entry_fee_pct": -1},
        {"closed_at": 99},
    ],
)
def test_invalid_cash_position_rejected(change):
    p = dict(position("spot_futures"), **change)
    with pytest.raises(ValueError):
        normalize("spot_futures", p)


def test_reverse_borrowing_and_unproven_inventory_rejected():
    p = position("spot_futures")
    p["direction"] = "SHORT_SPOT_LONG_FUTURE"
    with pytest.raises(ValueError, match="BORROWING"):
        normalize("spot_futures", p)
    p = position("spot_spot")
    p.pop("inventory_evidence")
    with pytest.raises(ValueError, match="INVENTORY"):
        normalize("spot_spot", p)


def test_spot_source_buyback_cannot_spend_other_venue_cash():
    data = books("spot_spot")
    data[-1]["asks"] = [[200, 10]]
    p = position("spot_spot")
    p["inventory_evidence"]["sell_quote"] = 0
    r = simulate("spot_spot", p, Tape(data), BASE)
    assert r["net"] is None and r["status"] == "RESIDUAL_EXPOSURE"
    assert r["residual"][1] < -0.4
    assert all(x >= -1e-10 for x in r["quote_balances"])
    assert any(o["reason"] == "QUOTE_FUNDS_LIMIT" for o in r["orders"])


def test_repeated_receipt_of_same_snapshot_does_not_refresh_depth():
    data = books("spot_futures")
    data[-1]["asks"] = [[107, 0.4]]
    duplicate = copy.deepcopy(data[-1])
    duplicate["received_at"] = 110.5
    r = simulate(
        "spot_futures", position("spot_futures"), Tape(data + [duplicate]), BASE
    )
    assert r["residual"] == pytest.approx([0.6, -0.6])
    assert r["net"] is None


@pytest.mark.parametrize(
    "field,value", [("exchange", "other"), ("symbol", "OTHER/USDT")]
)
def test_instrument_route_identity_cannot_be_relabelled(field, value):
    data = books("spot_futures")
    data[0]["instrument"][field] = value
    r = simulate("spot_futures", position("spot_futures"), Tape(data), BASE)
    assert r["status"] == "NO_ENTRY"
    assert r["orders"][0]["reason"] == "INSTRUMENT_MISMATCH"


def seed(path, strategy):
    p = position(strategy)
    table = "spot_future_paper" if strategy == "spot_futures" else "spot_spot_paper"
    fields = (
        ("id", "opened_at", "closed_at", "status", "base", "exchange", "direction")
        if strategy == "spot_futures"
        else ("id", "opened_at", "closed_at", "status", "symbol", "buy", "sell")
    )
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table}({','.join(fields)},payload TEXT)")
        db.execute(
            f"INSERT INTO {table} VALUES({','.join('?' for _ in range(len(fields)+1))})",
            [p[k] for k in fields] + [json.dumps(p)],
        )
        db.execute(
            "CREATE TABLE market_books(id INTEGER PRIMARY KEY,received_at REAL,payload TEXT)"
        )
        db.executemany(
            "INSERT INTO market_books(received_at,payload) VALUES(?,?)",
            [(b["received_at"], json.dumps(b)) for b in books(strategy)],
        )


@offline_async
@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
async def test_durable_reproducible_report_and_export(tmp_path, strategy):
    path = tmp_path / "db.sqlite"
    seed(path, strategy)
    report = await build(path, strategy, clock=lambda: 120)
    assert report["status"] == "MODELED" and report["sample_size"] == 1
    assert len(report["scenarios"]) == 3 and "NET" in render(report)
    with sqlite3.connect(path) as db:
        saved = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM cash_execution_results")
        ]
        assert len(saved) == 3
        db.execute("DELETE FROM market_books")
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%ledger%'"
        ).fetchall()
    r = saved[0]
    evidence = [o["book_evidence"] for o in r["orders"] if "book_evidence" in o]
    assert simulate(strategy, r["position"], Tape(evidence), BASE)["net"] == r["net"]
    from app.audit_export import build as export

    _, archive = await export(path, tmp_path / "export")
    with zipfile.ZipFile(archive) as z:
        assert "cash_execution_runs.csv" in z.namelist()
        assert "cash_execution_results.csv" in z.namelist()


@offline_async
@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
async def test_conflicting_history_does_not_generate_results(tmp_path, strategy):
    path = tmp_path / "db.sqlite"
    seed(path, strategy)
    b = books(strategy)[0]
    b["asks"] = [[101, 10]]
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO market_books(received_at,payload) VALUES(?,?)",
            (100, json.dumps(b)),
        )
    r = await build(path, strategy)
    assert r["status"] == "BOOK_HISTORY_INVALID" and r["sample_size"] == 0
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM cash_execution_results").fetchone()[0] == 0
        )


@offline_async
async def test_legacy_inventory_excluded_and_readonly_run_does_not_create_tables(
    tmp_path,
):
    path = tmp_path / "db.sqlite"
    seed(path, "spot_spot")
    p = position("spot_spot")
    p.pop("inventory_evidence")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE spot_spot_paper SET payload=?", (json.dumps(p),))
    r = await build(path, "spot_spot", persist=False)
    assert r["excluded"] == {"POSITION_OR_INVENTORY_UNVERIFIED": 1}
    assert r["sample_size"] == 0
    with sqlite3.connect(path) as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='cash_execution_runs'"
        ).fetchall()


def test_telegram_buttons_use_highlight_and_runtime_routes():
    from app.tg_ui import replay_menu

    buttons = [b for row in replay_menu("sf_execution").inline_keyboard for b in row]
    assert (
        next(b for b in buttons if b.callback_data == "sf_execution").style == "primary"
    )
    from app.main import keyboard_for

    assert any(
        b.callback_data == "ss_execution"
        for row in keyboard_for("ss_execution").inline_keyboard
        for b in row
    )


@offline_async
async def test_report_and_results_rollback_together(tmp_path):
    from app.cash_execution_replay import SCHEMA

    path = tmp_path / "db.sqlite"
    seed(path, "spot_futures")
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "CREATE TRIGGER fail_second_result BEFORE INSERT ON cash_execution_results WHEN NEW.scenario='ASYMMETRIC' BEGIN SELECT RAISE(ABORT,'offline failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="offline failure"):
        await build(path, "spot_futures")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM cash_execution_runs").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM cash_execution_results").fetchone()[0] == 0
        )


@offline_async
@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot"])
async def test_telegram_screen_builds_strategy_report_without_runtime(
    tmp_path, monkeypatch, strategy
):
    import app.main as main
    from types import SimpleNamespace

    path = tmp_path / "db.sqlite"
    seed(path, strategy)
    monkeypatch.setattr(main, "config", SimpleNamespace(db_path=path))
    screen = "sf_execution" if strategy == "spot_futures" else "ss_execution"
    text = await main.text_for(screen)
    assert "Сделок: 1" in text and "NET" in text


@pytest.mark.parametrize(
    "change",
    [dict(sell_base=0.5), dict(buy_quote=1), dict(sell_quote=-1), dict(asset="Y")],
)
def test_inventory_proof_requires_owned_asset_and_both_quote_accounts(change):
    p = position("spot_spot")
    p["inventory_evidence"].update(change)
    with pytest.raises(ValueError):
        normalize("spot_spot", p)
