import asyncio
import copy
import json
import sqlite3
import zipfile
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from app.execution_book_replay import (
    Tape as BookTape,
    Scenario,
    simulate as ff_simulate,
)
from app.funding_execution_replay import SCHEMA, build, normalize, render, simulate
from app.funding_rate_history import Store, Tape as RateTape, make_window, validate
from app.funding_paper_source import Source
from app.secondary_book_history import spec
from tests.test_cash_execution_replay import offline_async
from tests.test_execution_book_replay import books, book, position as ff_position, FEES
from tests.test_secondary_book_history import market

BASE = Scenario("BASE", 0, 0)
SYMBOL = "X/USDT:USDT"


def position(first=1005):
    fees = 210 * 0.0005
    funding = 1.0 if first <= 1010 else 0.0
    net = 7 - fees - 211 * 0.0005 - 0.1 + funding
    return dict(
        id=1,
        symbol=SYMBOL,
        buy="binance",
        sell="bybit",
        base_qty=1,
        opened_at=1000,
        closed_at=1010,
        status="CLOSED",
        entry_buy=100,
        entry_sell=110,
        entry_fee_pct=0.2,
        entry_fees=fees,
        safety=0.1,
        net=net,
        long_next=first,
        short_next=first,
        long_interval=1,
        short_interval=1,
        last_mark=dict(
            mode="FUNDING_PUBLIC_HISTORY_MODEL",
            funding_known=True,
            ts=1010,
            base_qty=1,
            gross=7,
            entry_fees=fees,
            exit_fees=211 * 0.0005,
            safety=0.1,
            funding=funding,
            net=net,
        ),
    )


def windows(p=None, observed=1050):
    p = p or position()
    out = []
    for key, prefix, rate in (("buy", "long", 0.001), ("sell", "short", 0.01)):
        rows = [
            dict(symbol=SYMBOL, timestamp=p[prefix + "_next"] * 1000, fundingRate=rate)
        ]
        out.append(
            make_window(
                p, p[key], prefix, rows, market(SYMBOL, contractSize=1), observed
            )
        )
    return out


def replay(p=None, data=None, rates=None, scenario=BASE):
    p = p or position()
    return simulate(
        p,
        BookTape(data or books()),
        RateTape(windows(p) if rates is None else rates, 1060),
        scenario,
    )


def test_closed_model_accounts_per_leg_funding_and_reconciles_without_double_slippage():
    r = replay()
    assert r["status"] == "CLOSED" and r["funding_known"]
    assert r["funding"] == pytest.approx(1)
    assert r["net"] == pytest.approx(7 - 0.2105 - 0.1 + 1)
    assert [e["base_exposure"] for e in r["funding_events"]] == [1, -1]
    a = r["attribution"]
    assert a["net"] == pytest.approx(
        a["basis"] - a["fees"] - a["safety"] + a["funding"]
    )
    assert a["adverse_execution_delta"] == 0
    assert not r["release_authorized"]


def test_delayed_second_leg_misses_settlement_without_forecast_credit():
    p = position(1000.25)
    fast = replay(p)
    slow = replay(p, scenario=Scenario("LAG", 0, 0.5))
    assert fast["funding"] == pytest.approx(1)
    assert slow["funding"] == pytest.approx(-0.1)
    assert [e["venue"] for e in slow["funding_events"]] == ["binance"]
    p.update(projected_carry_pct=99999, short_rate=0.99, long_rate=-0.99)
    assert replay(p, scenario=Scenario("LAG", 0, 0.5))["net"] == slow["net"]


def test_delayed_exit_charges_funding_only_on_remaining_leg():
    p = position(1010.25)
    assert replay(p)["funding"] == 0
    slow = replay(p, scenario=Scenario("LAG", 0, 0.5))
    assert slow["funding"] == pytest.approx(1.1)
    assert [e["venue"] for e in slow["funding_events"]] == ["bybit"]


def test_partial_entry_settlement_uses_actual_filled_exposure():
    p = position(1000.25)
    data = books()[:2]
    data[1]["bids"] = [[110, 0.5]]
    r = replay(p, data)
    assert r["status"] == "ENTRY_ABORT_FLAT"
    assert r["funding"] == pytest.approx(0.45)
    assert r["attribution"]["reference_paper_basis"] is None
    assert [e["base_exposure"] for e in r["funding_events"]] == [1, -0.5]


def test_partial_exit_recovery_charges_only_remaining_quantity():
    p = position(1010.75)
    data = books()
    data[2]["bids"] = [[104, 0.4]]
    data.append(book("binance", 1011, 103, 104, depth=0.6))
    r = replay(p, data)
    assert r["status"] == "CLOSED_WITH_RECOVERY"
    assert r["funding"] == pytest.approx(-0.06)
    assert r["funding_events"][0]["base_exposure"] == pytest.approx(0.6)
    assert r["attribution"]["adverse_execution_delta"] == pytest.approx(0.6)
    assert r["net"] == pytest.approx(6.4 - r["fees"] - 0.1 - 0.06)


def test_unclosed_exposure_never_receives_final_funding_or_net():
    data = books()
    data[2]["bids"] = [[104, 0.4]]
    r = replay(data=data)
    assert r["status"] == "RESIDUAL_EXPOSURE" and r["net"] is None
    assert r["funding"] is None and not r["funding_known"]
    assert r["residual"]["binance"] == pytest.approx(0.6)
    assert r["attribution"] is None


def test_missing_or_short_history_blocks_net_even_after_flat_exit():
    for rates in ([], windows()[:1]):
        r = replay(rates=rates)
        assert r["execution_status"] == "CLOSED"
        assert r["status"] == "FUNDING_ACCOUNTING_UNKNOWN"
        assert r["net"] is None and r["net_without_funding"] is not None
    p = position(1010.25)
    short = windows(p, observed=1040)
    r = replay(p, rates=short, scenario=Scenario("LAG", 0, 0.5))
    assert r["net"] is None


def test_settlement_at_fill_boundary_is_explicitly_ambiguous():
    r = replay(position(1000.5), scenario=Scenario("LAG", 0, 0.5))
    assert (
        r["net"] is None and r["funding_reason"] == "FUNDING_ORDER_BOUNDARY_AMBIGUOUS"
    )


def test_conflicting_overlapping_rate_windows_do_not_select_better_income():
    data = windows()
    extra = copy.deepcopy(data[1])
    extra["observed_at"] += 1
    extra["events"][0]["rate"] = 0.02
    r = replay(rates=data + [extra])
    assert r["net"] is None and r["funding_reason"] == "FUNDING_HISTORY_CONFLICT"


def test_future_observation_is_not_available_to_current_report():
    assert replay(rates=windows(observed=1070))["net"] is None


def test_reported_settlement_time_is_retained_and_used_instead_of_calendar_rounding():
    p = position(1000.25)
    shifted = windows(p)
    for w in shifted:
        w["events"][0]["reported_ts"] = 1000.35
    r = replay(p, rates=shifted, scenario=Scenario("LAG", 0, 0.3))
    assert r["funding"] == pytest.approx(1)
    assert all(
        e["ts"] == 1000.35 and e["calendar_ts"] == 1000.25 for e in r["funding_events"]
    )
    assert replay(p, scenario=Scenario("LAG", 0, 0.3))["funding"] == pytest.approx(-0.1)


def test_duplicate_settlement_with_conflicting_reported_time_blocks_net():
    data = windows()
    duplicate = copy.deepcopy(data[1])
    duplicate["events"][0]["reported_ts"] += 0.5
    r = replay(rates=data + [duplicate])
    assert r["net"] is None and r["funding_reason"] == "FUNDING_HISTORY_CONFLICT"


def test_negative_rates_reverse_payment_sign_without_forecast_substitution():
    rates = windows()
    rates[0]["events"][0]["rate"] = -0.001
    rates[1]["events"][0]["rate"] = -0.01
    r = replay(rates=rates)
    assert r["funding"] == pytest.approx(-1)
    assert [e["amount"] for e in r["funding_events"]] == pytest.approx([0.1, -1.1])


def test_funding_instrument_must_match_execution_book_metadata():
    data = windows()
    data[0]["instrument"]["contract_size"] = 0.1
    r = replay(rates=data)
    assert r["net"] is None and r["funding_reason"] == "FUNDING_BOOK_IDENTITY_CONFLICT"


def test_no_entry_receives_no_funding_without_history():
    r = simulate(position(), BookTape([]), RateTape([], 1060), BASE)
    assert r["status"] == "NO_ENTRY" and r["funding"] == 0 and r["net"] is None


@pytest.mark.parametrize(
    "change",
    [
        dict(status="OPEN"),
        dict(entry_fees=2),
        dict(entry_fee_pct=True),
        dict(base_qty=0),
        dict(net=999),
    ],
)
def test_invalid_paper_lineage_is_excluded(change):
    with pytest.raises(ValueError):
        normalize(dict(position(), **change))


@pytest.mark.parametrize(
    "change",
    [
        dict(covered_until=1049),
        dict(events=[]),
        dict(first_settlement=1000),
        dict(interval_seconds=0),
        dict(instrument={}),
        dict(mode="FORECAST"),
    ],
)
def test_window_requires_mature_complete_calendar_and_instrument(change):
    with pytest.raises((ValueError, KeyError)):
        validate(dict(windows()[0], **change))


def test_identical_duplicate_rates_are_deduplicated_but_conflicts_and_gaps_rejected():
    p = position()
    rows = [dict(symbol=SYMBOL, timestamp=1005000, fundingRate=0.001)]
    w = make_window(
        p, "binance", "long", rows * 2, market(SYMBOL, contractSize=1), 1050
    )
    assert len(w["events"]) == 1
    for wrong in (
        [dict(rows[0], fundingRate=0.002)],
        [dict(rows[0], timestamp=1007000)],
        [dict(rows[0], symbol="Y/USDT:USDT")],
    ):
        with pytest.raises(ValueError):
            make_window(
                p, "binance", "long", rows + wrong, market(SYMBOL, contractSize=1), 1050
            )


def test_futures_replay_cannot_replenish_depth_from_repeated_snapshot():
    data = books()
    data[2]["bids"] = [[104, 0.4]]
    duplicate = copy.deepcopy(data[2])
    duplicate["received_at"] = 1010.5
    r = ff_simulate(
        ff_position(), BookTape(data + [duplicate]), BASE, FEES, recovery_rounds=3
    )
    assert r["residual"]["binance"] == pytest.approx(0.6)
    assert r["net"] is None


class Client:
    has = {"fetchFundingRateHistory": True}

    def __init__(self, rate):
        self.rate = rate
        self.calls = 0

    def market(self, symbol):
        return market(symbol, contractSize=1)

    async def fetch_funding_rate_history(self, *args):
        self.calls += 1
        return [dict(symbol=SYMBOL, timestamp=1005000, fundingRate=self.rate)]


@offline_async
async def test_source_records_full_mature_window_without_extra_fetches(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    await store.init()
    clients = dict(binance=Client(0.001), bybit=Client(0.01))
    source = Source(clients, None, 5, clock=lambda: 1050, history_store=store)
    h = await source.settlements(position(), 1010)
    assert h.verified and h.amount == pytest.approx(1)
    assert [c.calls for c in clients.values()] == [1, 1]
    with sqlite3.connect(store.path) as db:
        payloads = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM funding_rate_windows")
        ]
    assert len(payloads) == 2 and all(w["covered_until"] == 1020 for w in payloads)
    assert store.recorded == 2 and store.failures == 0


@offline_async
async def test_history_recording_failure_does_not_rewrite_paper_income(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    await store.init()
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE funding_rate_windows")
    source = Source(
        dict(binance=Client(0.001), bybit=Client(0.01)),
        None,
        5,
        clock=lambda: 1050,
        history_store=store,
    )
    h = await source.settlements(position(), 1010)
    assert h.verified and h.amount == pytest.approx(1)
    assert store.failures == 1 and store.recorded == 0


@offline_async
async def test_bad_second_window_writes_neither_venue(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    await store.init()
    clients = dict(binance=Client(0.001), bybit=Client(0.01))
    with pytest.raises(ValueError):
        await store.capture(
            position(),
            {
                "binance": await clients["binance"].fetch_funding_rate_history(),
                "bybit": [],
            },
            clients,
            1050,
        )
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM funding_rate_windows").fetchone()[0] == 0
        )


@offline_async
async def test_window_retention_and_database_failure_keep_pair_atomic(tmp_path):
    store = Store(tmp_path / "db.sqlite", max_rows=2)
    await store.init()
    clients = dict(binance=Client(0.001), bybit=Client(0.01))
    histories = {v: await c.fetch_funding_rate_history() for v, c in clients.items()}
    await store.capture(position(), histories, clients, 1050)
    await store.capture(position(), histories, clients, 1051)
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT COUNT(*),MIN(observed_at) FROM funding_rate_windows"
        ).fetchone() == (2, 1051)
        db.execute(
            "CREATE TRIGGER fail_window BEFORE INSERT ON funding_rate_windows WHEN NEW.venue='bybit' BEGIN SELECT RAISE(ABORT,'offline failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await store.capture(position(), histories, clients, 1052)
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT COUNT(*),MAX(observed_at) FROM funding_rate_windows"
        ).fetchone() == (2, 1051)
    assert store.recorded == 4


@offline_async
async def test_corrupt_rate_history_or_paper_identity_does_not_create_profit(tmp_path):
    path = tmp_path / "db.sqlite"
    seed(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE funding_rate_windows SET payload='not json'")
    r = await build(path, clock=lambda: 1060)
    assert r["status"] == "HISTORY_INVALID" and r["sample_size"] == 0
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM funding_execution_results").fetchone()[0]
            == 0
        )
        db.execute("UPDATE funding_paper SET buy='other'")
    r = await build(path, clock=lambda: 1060)
    assert r["sample_size"] == 0 and r["excluded"] == {
        "POSITION_OR_ACCOUNTING_UNVERIFIED": 1
    }


def test_system_screen_exposes_funding_recording_health():
    from app.tg_system_center import render as system

    scanner = NS(paused=False, clients={}, ids=[], last_scan=None)
    runtime = NS(counts=lambda: {})
    secondary = NS(
        clients={},
        funding_paper=NS(source=NS(history_store=NS(recorded=8, failures=2))),
    )
    text = system(scanner, runtime, secondary)
    assert "8 проверенных окон" in text and "проверки: 2" in text


class BookClient:
    def __init__(self, now):
        self.now = now

    def market(self, symbol):
        return market(symbol, contractSize=0.5)

    async def fetch_order_book(self, symbol, **kwargs):
        self.now[0] += 0.2
        return dict(
            symbol=symbol,
            bids=[[99, 2]],
            asks=[[100, 2]],
            timestamp=1000000,
            received_at=self.now[0] - 0.1,
            data_source="REST",
        )


class Funding:
    async def get(self, *args):
        return NS(rate=0.01, interval_hours=1, next_ts=1005000)


@offline_async
@pytest.mark.parametrize(
    "change",
    [
        dict(contract=1),
        dict(linear="yes"),
        dict(contractSize=True),
        dict(active=False),
        dict(spot=True),
    ],
)
async def test_funding_public_quote_requires_explicit_derivative_metadata(change):
    now = [1000]

    class Bad(BookClient):
        def market(self, symbol):
            return dict(super().market(symbol), **change)

    source = Source(
        dict(binance=Bad(now), bybit=BookClient(now)),
        Funding(),
        5,
        clock=lambda: now[0],
    )
    assert not (await source.quote(SYMBOL, "binance", "bybit"))["ok"]


@offline_async
async def test_funding_public_quote_rejects_contract_size_changed_during_fetch():
    now = [1000]

    class Changed(BookClient):
        size = 1

        def market(self, symbol):
            return market(symbol, contractSize=self.size)

        async def fetch_order_book(self, symbol, **kwargs):
            self.size = 0.5
            return await super().fetch_order_book(symbol, **kwargs)

    source = Source(
        dict(binance=Changed(now), bybit=BookClient(now)),
        Funding(),
        5,
        clock=lambda: now[0],
    )
    result = await source.quote(SYMBOL, "binance", "bybit")
    assert not result["ok"] and result["reason"] == "FUNDING_INSTRUMENT_CHANGED"


@offline_async
async def test_funding_quote_records_exact_base_unit_books_and_decision_after_receipt(
    tmp_path,
):
    from app.book_history import Store as Books

    now = [1000]
    store = Books(tmp_path / "db.sqlite", clock=lambda: now[0])
    await store.init()
    source = Source(
        dict(binance=BookClient(now), bybit=BookClient(now)),
        Funding(),
        5,
        clock=lambda: now[0],
        book_history=store,
    )
    result = await source.quote(SYMBOL, "binance", "bybit")
    assert result["ok"] and result["ts"] == pytest.approx(1000.4)
    assert result["market_ts"] == 1000 and result["received_at"] == pytest.approx(
        1000.3
    )
    with sqlite3.connect(store.path) as db:
        saved = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM market_books ORDER BY id")
        ]
    assert len(saved) == 2 and source.book_recorded == 2
    assert [w["received_at"] for w in saved] == pytest.approx([1000.1, 1000.3])
    assert all(
        w["asks"] == [[100, 1]] and w["instrument"]["contract_size"] == 0.5
        for w in saved
    )


@offline_async
@pytest.mark.parametrize("mode", ["failure", "slow"])
async def test_book_recording_error_is_visible_and_slow_write_cannot_retime_price(mode):
    now = [1000]

    class Books:
        async def record(self, *args):
            if mode == "failure":
                raise OSError("offline")
            now[0] += 2
            return 2

    source = Source(
        dict(binance=BookClient(now), bybit=BookClient(now)),
        Funding(),
        5,
        clock=lambda: now[0],
        book_history=Books(),
    )
    result = await source.quote(SYMBOL, "binance", "bybit")
    if mode == "failure":
        assert result["ok"] and source.book_record_failures == 1
    else:
        assert (
            not result["ok"] and result["reason"] == "FUNDING_BOOK_STALE_AFTER_RECORD"
        )


@offline_async
async def test_decision_time_does_not_hide_old_market_after_settlement_lookup(tmp_path):
    from tests.test_funding_paper_runtime import engine

    paper, feed = await engine(tmp_path)
    from tests.test_funding_paper_runtime import CANDIDATE

    await paper.cycle([CANDIDATE])
    feed.now = 1001
    quote, settlements = feed.quote, feed.settlements

    async def observed(*args):
        x = await quote(*args)
        return dict(x, market_ts=x["ts"] - 1)

    async def slow(*args):
        result = await settlements(*args)
        feed.now += 0.75
        return result

    feed.quote, feed.settlements = observed, slow
    assert not await paper.cycle([], False)
    assert paper.positions["1"]["data_reason"] == "BOOK_STALE_AFTER_HISTORY"
    assert "last_mark" not in paper.positions["1"]


def seed(path, p=None, rate_windows=True):
    from app.funding_paper import SCHEMA as PAPER_SCHEMA
    from app.funding_rate_history import SCHEMA as RATE_SCHEMA
    from app.book_history import SCHEMA as BOOK_SCHEMA

    p = p or position()
    with sqlite3.connect(path) as db:
        db.executescript(PAPER_SCHEMA + RATE_SCHEMA + BOOK_SCHEMA)
        db.execute(
            "INSERT INTO funding_paper VALUES(?,?,?,?,?,?,?,?,?)",
            (
                p["id"],
                p["symbol"],
                p["buy"],
                p["sell"],
                p["opened_at"],
                p["closed_at"],
                p["status"],
                p["net"],
                json.dumps(p),
            ),
        )
        for b in books():
            db.execute(
                "INSERT INTO market_books(venue,symbol,book_ts,received_at,payload) VALUES(?,?,?,?,?)",
                (
                    b["venue"],
                    b["symbol"],
                    b["book_ts"],
                    b["received_at"],
                    json.dumps(b),
                ),
            )
        if rate_windows:
            for w in windows(p):
                db.execute(
                    "INSERT INTO funding_rate_windows(venue,symbol,opened_at,covered_until,observed_at,payload) VALUES(?,?,?,?,?,?)",
                    (
                        w["venue"],
                        w["symbol"],
                        w["opened_at"],
                        w["covered_until"],
                        w["observed_at"],
                        json.dumps(w),
                    ),
                )


@offline_async
async def test_durable_results_export_and_reproduction_after_pruning(tmp_path):
    path = tmp_path / "db.sqlite"
    seed(path)
    r = await build(path, clock=lambda: 1060)
    assert r["sample_size"] == 1 and r["status"] == "MODELED"
    assert len(r["scenarios"]) == 3 and all(s["completed"] == 1 for s in r["scenarios"])
    assert "NET" in render(r)
    with sqlite3.connect(path) as db:
        saved = json.loads(
            db.execute(
                "SELECT payload FROM funding_execution_results WHERE scenario='BASE'"
            ).fetchone()[0]
        )
        db.execute("DELETE FROM market_books")
        db.execute("DELETE FROM funding_rate_windows")
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%ledger%'"
        ).fetchall()
    proof = [o["book_evidence"] for o in saved["orders"] if "book_evidence" in o]
    again = simulate(
        saved["position"], BookTape(proof), RateTape(saved["rate_windows"], 1060), BASE
    )
    assert (
        again["net"] == saved["net"]
        and again["funding_events"] == saved["funding_events"]
    )
    from app.audit_export import build as export

    _, archive = await export(path, tmp_path / "export")
    with zipfile.ZipFile(archive) as z:
        assert {
            "funding_execution_runs.csv",
            "funding_execution_results.csv",
            "funding_rate_windows.csv",
        } <= set(z.namelist())


@offline_async
async def test_legacy_rates_do_not_produce_final_net_and_readonly_does_not_create_tables(
    tmp_path,
):
    path = tmp_path / "db.sqlite"
    seed(path, rate_windows=False)
    r = await build(path, persist=False, clock=lambda: 1060)
    assert (
        r["scenarios"][0]["net"] is None and r["scenarios"][0]["funding_unknown"] == 1
    )
    with sqlite3.connect(path) as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='funding_execution_runs'"
        ).fetchall()


@offline_async
async def test_report_and_results_are_atomic_on_failed_second_row(tmp_path):
    path = tmp_path / "db.sqlite"
    seed(path)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "CREATE TRIGGER fail_result BEFORE INSERT ON funding_execution_results WHEN NEW.scenario='ASYMMETRIC' BEGIN SELECT RAISE(ABORT,'offline failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await build(path, clock=lambda: 1060)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM funding_execution_runs").fetchone()[0] == 0
        )
        assert (
            db.execute("SELECT COUNT(*) FROM funding_execution_results").fetchone()[0]
            == 0
        )


@offline_async
async def test_telegram_runtime_and_highlighted_button(tmp_path, monkeypatch):
    import app.main as main
    from app.tg_ui import replay_menu

    path = tmp_path / "db.sqlite"
    seed(path)
    monkeypatch.setattr(main, "config", NS(db_path=path))
    text = await main.text_for("fund_execution")
    assert "Сделок: 1" in text and "начисления" in text
    buttons = [b for row in replay_menu("fund_execution").inline_keyboard for b in row]
    assert (
        next(b for b in buttons if b.callback_data == "fund_execution").style
        == "primary"
    )
    assert any(
        b.callback_data == "fund_execution"
        for row in main.keyboard_for("fund_execution").inline_keyboard
        for b in row
    )
