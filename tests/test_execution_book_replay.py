import asyncio, copy, json, sqlite3, time
from dataclasses import asdict
import pytest
from app.book_history import Store, valid_book
from app.execution_book_replay import Scenario, Tape, simulate, build, render
from app.engine import Quote, Scanner, evaluate
from app.instruments import from_market
from app.discovery import RotatingUniverse
from app.db import Diary
from app.execution_sim import walk
from app.audit_export import build as export

SYMBOL = "X/USDT:USDT"


def spec(venue, size=1):
    return from_market(
        venue,
        dict(
            symbol=SYMBOL,
            base="X",
            quote="USDT",
            settle="USDT",
            contract=True,
            linear=True,
            contractSize=size,
        ),
    )


def book(venue, ts, bid, ask, depth=10, received=None):
    return dict(
        mode="PUBLIC_REST_BASE_UNITS",
        venue=venue,
        symbol=SYMBOL,
        book_ts=ts,
        received_at=ts if received is None else received,
        bids=[[bid, depth]],
        asks=[[ask, depth]],
        instrument=asdict(spec(venue)),
    )


def position():
    return dict(
        id=1,
        symbol=SYMBOL,
        buy="binance",
        sell="bybit",
        opened_at=1000,
        closed_at=1010,
        base_qty=1,
        safety_usd=0.1,
    )


def books():
    return [
        book("binance", 1000, 99, 100),
        book("bybit", 1000, 110, 111),
        book("binance", 1010, 104, 105),
        book("bybit", 1010, 106, 107),
    ]


FEES = dict(binance=0.0005, bybit=0.0005)


def test_complete_round_trip_uses_actual_model_volume_and_all_fees():
    result = simulate(position(), Tape(books()), Scenario("base", 0, 0), FEES)
    assert result["status"] == "CLOSED" and result["net"] == pytest.approx(
        7 - 0.2105 - 0.1
    )
    assert not result["release_authorized"] and result["funding"] == 0
    assert len(result["orders"]) == 4 and all(
        x == 0 for x in result["residual"].values()
    )


def test_tape_never_reads_future_received_data_even_with_old_exchange_time():
    b = books() + [book("bybit", 1000, 999, 1000, received=1000.1)]
    result = simulate(position(), Tape(b), Scenario("base", 0, 0), FEES)
    assert result["orders"][1]["price"] == 110
    tape = Tape([book("binance", 1000, 99, 100, received=1000.2)])
    assert tape.at("binance", SYMBOL, 1000.1, 1.5) is None
    assert tape.at("binance", SYMBOL, 1000.2, 1.5)


def test_asymmetric_latency_changes_fill_and_does_not_change_baseline():
    b = books() + [book("bybit", 1000.3, 109, 110), book("bybit", 1010.3, 107, 108)]
    tape = Tape(b)
    base = simulate(position(), tape, Scenario("base", 0, 0), FEES)
    slow = simulate(position(), tape, Scenario("slow", 0.15, 0.5), FEES)
    assert slow["status"] == "CLOSED" and slow["net"] < base["net"]
    assert slow["orders"][1]["price"] == 109 and slow["orders"][3]["price"] == 108


def test_partial_entry_flattens_both_filled_legs_and_records_costs():
    b = [book("binance", 1000, 99, 100), book("bybit", 1000, 110, 111, depth=0.5)]
    result = simulate(position(), Tape(b), Scenario("base", 0, 0), FEES)
    assert result["status"] == "ENTRY_ABORT_FLAT" and result["net"] == pytest.approx(
        -1.5 - 0.15475 - 0.1
    )
    assert [x["filled"] for x in result["orders"]] == [1, 0.5, 1, 0.5]
    assert all(x == 0 for x in result["residual"].values())


def test_missing_second_leg_uses_later_recovery_book():
    tape = Tape(
        [
            book("binance", 1000, 99, 100),
            book("bybit", 1000, 110, 111),
            book("binance", 1002.5, 98, 99),
        ]
    )
    result = simulate(position(), tape, Scenario("late", 0, 2), FEES)
    assert result["status"] == "ENTRY_ABORT_FLAT" and result["net"] == pytest.approx(
        -2 - 0.099 - 0.1
    )
    assert result["orders"][1]["reason"] == "BOOK_UNAVAILABLE_OR_STALE"
    assert (
        result["orders"][2]["arrival"] == 1002.5 and result["orders"][2]["price"] == 98
    )


def test_partial_exit_and_failed_recovery_preserve_residual_without_final_net():
    b = books()
    b[2]["bids"] = [[104, 0.3]]
    b.append(book("binance", 1010.5, 103, 104, depth=0.4))
    result = simulate(position(), Tape(b), Scenario("base", 0, 0), FEES)
    assert result["status"] == "RESIDUAL_EXPOSURE" and result["net"] is None
    assert result["residual"]["binance"] == pytest.approx(0.3)
    assert result["cashflow"] != 0 and result["fees"] > 0


def test_stale_exit_cannot_be_a_profitable_closed_trade():
    result = simulate(position(), Tape(books()[:2]), Scenario("base", 0, 0), FEES)
    assert result["status"] == "RESIDUAL_EXPOSURE" and result["net"] is None
    assert result["residual"] == dict(binance=1, bybit=-1)


@pytest.mark.parametrize(
    "case",
    [
        "nan",
        "negative",
        "unordered",
        "crossed",
        "future",
        "linear",
        "contract",
        "identity",
    ],
)
def test_invalid_snapshots_fail_closed(case):
    b = book("binance", 1000, 99, 100)
    if case == "nan":
        b["asks"][0][0] = float("nan")
    if case == "negative":
        b["bids"][0][1] = -1
    if case == "unordered":
        b["asks"] = [[101, 1], [100, 1]]
    if case == "crossed":
        b["bids"][0][0] = 101
    if case == "future":
        b["book_ts"] = 1001
    if case == "linear":
        b["instrument"]["linear"] = False
    if case == "contract":
        b["instrument"]["contract_size"] = 0
    if case == "identity":
        b["instrument"]["base"] = ""
    assert not valid_book(b)
    with pytest.raises(ValueError):
        Tape([b])


def test_conflicting_snapshot_and_contract_change_cannot_prove_exit():
    b = books()
    different = copy.deepcopy(b[0])
    different["asks"] = [[101, 10]]
    with pytest.raises(ValueError, match="CONFLICT"):
        Tape([b[0], different])
    b[2]["instrument"]["contract_size"] = 2
    result = simulate(position(), Tape(b), Scenario("base", 0, 0), FEES)
    assert result["status"] == "RESIDUAL_EXPOSURE" and result["net"] is None
    assert result["orders"][2]["reason"] == "INSTRUMENT_CHANGED"


@pytest.mark.parametrize("qty", [0, -1, float("nan"), float("inf")])
def test_simulator_rejects_invalid_quantity(qty):
    with pytest.raises(ValueError):
        walk([[100, 1]], qty)


def test_recording_prunes_by_age_and_count_and_survives_restart(tmp_path):
    async def go():
        path = str(tmp_path / "d.db")
        store = Store(path, max_rows=2, retention_seconds=10, clock=lambda: 1020)
        await store.init()
        specs = {"binance": {SYMBOL: spec("binance")}}
        qs = [
            Quote("binance", SYMBOL, [[99, 1]], [[100, 1]], ts, ts)
            for ts in (1000, 1011, 1012, 1013)
        ]
        await store.record(qs, specs)
        assert (await store.stats())["count"] == 2
        restored = Store(path)
        assert (await restored.stats()) == dict(count=2, first=1012, last=1013)
        with sqlite3.connect(path) as d:
            assert (
                json.loads(
                    d.execute("SELECT payload FROM market_books LIMIT 1").fetchone()[0]
                )["instrument"]["contract_size"]
                == 1
            )

    asyncio.run(go())


def test_scanner_records_base_units_and_decision_is_after_observation(tmp_path):
    async def go():
        class Client:
            async def fetch_order_book(self, *args, **kwargs):
                return dict(bids=[[99, 10]], asks=[[100, 10]])

        scanner = Scanner(["binance", "bybit"], 5, 12)
        scanner.clients = {v: Client() for v in scanner.ids}
        scanner.symbols = {v: {SYMBOL} for v in scanner.ids}
        scanner.specs = {v: {SYMBOL: spec(v, 0.1)} for v in scanner.ids}
        scanner.universe = RotatingUniverse(scanner.symbols)
        store = Store(str(tmp_path / "d.db"))
        await store.init()
        scanner.on_books = store.record
        scanner.watch_routes = {(SYMBOL, "binance", "bybit")}
        rows = await scanner.scan()
        assert rows and rows[0]["decision_at"] >= rows[0]["ts"]
        assert (await store.stats())["count"] == 2
        with sqlite3.connect(store.path) as d:
            payload = json.loads(
                d.execute("SELECT payload FROM market_books LIMIT 1").fetchone()[0]
            )
            assert payload["bids"][0][1] == 1

    asyncio.run(go())


def test_execution_report_is_durable_exported_and_never_credits_paper(tmp_path):
    async def go():
        path = str(tmp_path / "d.db")
        d = Diary(path)
        await d.init()
        store = Store(path)
        await store.init()
        p = position()
        with sqlite3.connect(path) as c:
            c.execute(
                "INSERT INTO paper_positions(id,symbol,buy,sell,opened_at,closed_at,status,base_qty,safety_usd,entry_fees_usd,current_net_usd) VALUES(?,?,?,?,?,?,?, ?,?,?,?)",
                (
                    1,
                    SYMBOL,
                    p["buy"],
                    p["sell"],
                    1000,
                    1010,
                    "CLOSED",
                    1,
                    0.1,
                    0.21,
                    999,
                ),
            )
            for b in books():
                c.execute(
                    "INSERT INTO market_books(venue,symbol,book_ts,received_at,payload) VALUES(?,?,?,?,?)",
                    (b["venue"], SYMBOL, b["book_ts"], b["received_at"], json.dumps(b)),
                )
        r = await build(path)
        assert (
            r["status"] == "MODELED"
            and r["sample_size"] == 1
            and not r["release_authorized"]
        )
        assert r["scenarios"][0]["completed"] == 1 and "REST" in render(r)
        with sqlite3.connect(path) as c:
            assert (
                c.execute("SELECT COUNT(*) FROM execution_replay_results").fetchone()[0]
                == 3
            )
            assert (
                c.execute("SELECT current_net_usd FROM paper_positions").fetchone()[0]
                == 999
            )
        workbook, archive = await export(path, tmp_path / "export")
        import zipfile

        with zipfile.ZipFile(archive) as z:
            assert (
                "execution_replay_results.csv" in z.namelist()
                and "market_books.csv" in z.namelist()
            )
        again = await build(path, persist=False)
        assert "run_id" not in again

    asyncio.run(go())


def test_empty_legacy_and_corrupt_history_return_explicit_status(tmp_path):
    async def go():
        path = str(tmp_path / "d.db")
        assert (await build(path))["status"] == "HISTORY_NOT_COLLECTED"
        d = Diary(path)
        await d.init()
        await Store(path).init()
        assert (await build(path))["status"] == "INSUFFICIENT_DATA"
        with sqlite3.connect(path) as c:
            c.execute(
                "INSERT INTO paper_positions(status,opened_at,closed_at) VALUES('CLOSED',1000,1010)"
            )
        r = await build(path)
        assert r["status"] == "INSUFFICIENT_DATA" and r["excluded"]

    asyncio.run(go())


def test_entry_that_arrives_after_exit_deadline_is_flattened():
    p = position()
    p["closed_at"] = 1000.2
    result = simulate(p, Tape(books()), Scenario("late", 0.5, 0.5), FEES)
    assert result["status"] == "LATE_ENTRY_ABORT_FLAT" and result["net"] < 0
    assert all(o["phase"] != "EXIT" for o in result["orders"])
    assert all(x == 0 for x in result["residual"].values())


def test_execution_result_write_failure_rolls_back_entire_run(tmp_path):
    async def go():
        path = str(tmp_path / "d.db")
        d = Diary(path)
        await d.init()
        await Store(path).init()
        with sqlite3.connect(path) as c:
            c.execute(
                "INSERT INTO paper_positions(symbol,buy,sell,opened_at,closed_at,status,base_qty,safety_usd,entry_fees_usd) VALUES(?,?,?,?,?,'CLOSED',1,.1,.21)",
                (SYMBOL, "binance", "bybit", 1000, 1010),
            )
            for b in books():
                c.execute(
                    "INSERT INTO market_books(venue,symbol,book_ts,received_at,payload) VALUES(?,?,?,?,?)",
                    (b["venue"], SYMBOL, b["book_ts"], b["received_at"], json.dumps(b)),
                )
            c.execute(
                "CREATE TRIGGER fail BEFORE INSERT ON execution_replay_results BEGIN SELECT RAISE(ABORT,'result write failed'); END"
            )
        with pytest.raises(Exception, match="result write failed"):
            await build(path)
        with sqlite3.connect(path) as c:
            assert (
                c.execute("SELECT COUNT(*) FROM execution_replay_runs").fetchone()[0]
                == 0
            )
            assert (
                c.execute("SELECT COUNT(*) FROM execution_replay_results").fetchone()[0]
                == 0
            )

    asyncio.run(go())


def test_execution_screen_and_main_replay_are_wired_without_network(
    tmp_path, monkeypatch
):
    import app.main as main
    from types import SimpleNamespace as NS

    async def go():
        path = str(tmp_path / "d.db")
        await Diary(path).init()
        await Store(path).init()
        monkeypatch.setattr(main, "config", NS(db_path=path, interval=30))
        assert "Исполнение" in await main.text_for("execution_replay")
        assert "Фьючерсы ↔ Фьючерсы" in await main.text_for("replay")
        callbacks = [
            b.callback_data
            for row in main.keyboard_for("execution_replay").inline_keyboard
            for b in row
        ]
        assert "execution_replay" in callbacks and "replay" in callbacks

    asyncio.run(go())


def test_saved_evidence_reproduces_result_after_book_retention_cleanup(tmp_path):
    async def go():
        path = str(tmp_path / "d.db")
        await Diary(path).init()
        await Store(path).init()
        with sqlite3.connect(path) as c:
            c.execute(
                "INSERT INTO paper_positions(symbol,buy,sell,opened_at,closed_at,status,base_qty,safety_usd,entry_fees_usd) VALUES(?,?,?,?,?,'CLOSED',1,.1,.21)",
                (SYMBOL, "binance", "bybit", 1000, 1010),
            )
            for b in books():
                c.execute(
                    "INSERT INTO market_books(venue,symbol,book_ts,received_at,payload) VALUES(?,?,?,?,?)",
                    (b["venue"], SYMBOL, b["book_ts"], b["received_at"], json.dumps(b)),
                )
        r = await build(path)
        with sqlite3.connect(path) as c:
            payload = json.loads(
                c.execute(
                    "SELECT payload FROM execution_replay_results WHERE scenario='BASE'"
                ).fetchone()[0]
            )
            c.execute("DELETE FROM market_books")
        reproduced = simulate(
            payload["position"],
            Tape([o["book_evidence"] for o in payload["orders"]]),
            Scenario(**r["scenarios"][0]["parameters"]),
            r["fee_rates"],
        )
        assert (
            reproduced["net"] == payload["net"]
            and reproduced["orders"] == payload["orders"]
        )

    asyncio.run(go())
