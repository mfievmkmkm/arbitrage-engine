import asyncio
import copy
import json
import aiosqlite
import pytest
from app.dex_execution_stress import History, Tape, Scenario, simulate, build, render
from app.cex_dex_paper import Engine
from app.project_readiness import build as readiness, render as render_readiness
from tests.test_cex_dex_roundtrip import setup


async def closed_fixture(tmp_path, reverse=False):
    now, q, source, route, _ = setup(reverse)
    history = History(tmp_path / "a.db", clock=lambda: now[0])
    await history.init()
    source.history = history
    e = Engine(history.path, source, clock=lambda: now[0])
    await e.init()
    await e.cycle(route)
    now[0] = 1001
    p = (await e.cycle(entry_enabled=False))[0]
    async with aiosqlite.connect(history.path) as d:
        async with d.execute("SELECT payload FROM dex_quote_history") as c:
            quotes = [json.loads(r[0]) for r in await c.fetchall()]
    books = [p["entry_cex"], p["last_mark"]["exit_cex"]]
    return p, quotes, books, history


@pytest.mark.parametrize("reverse", [False, True])
def test_normal_sequential_model_restores_raw_inventory_and_hedge(tmp_path, reverse):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path, reverse)
        result = simulate(p, Tape(quotes, books), Scenario("BASE"))
        assert result["status"] == "CLOSED_MODEL", result
        assert (
            result["cex_contracts_remaining"] == 0 and result["asset_delta_raw"] == "0"
        )
        assert result["net"] == pytest.approx(p["net"])
        assert not result["ledger_allowed"] and not result["live_allowed"]

    asyncio.run(run())


def test_partial_entry_unwinds_confirmed_contracts_and_whole_dex_inventory(tmp_path):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        books[0]["bids"][0][1] = 10
        result = simulate(p, Tape(quotes, books), Scenario("PARTIAL"))
        assert result["status"] == "CLOSED_MODEL"
        assert result["reason"] == "PARTIAL_ENTRY_UNWIND"
        assert (
            result["rows"][1]["filled"] == 10 and result["rows"][2]["requested"] == 10
        )
        assert result["asset_delta_raw"] == "0"

    asyncio.run(run())


def test_partial_exit_cannot_reuse_already_consumed_book_depth(tmp_path):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        books[1]["asks"][0][1] = 10
        result = simulate(p, Tape(quotes, books), Scenario("PARTIAL_EXIT"))
        assert result["status"] == "RESIDUAL_MODEL" and result["net"] is None
        assert result["cex_contracts_remaining"] == -39
        recovery = [r for r in result["rows"] if r["stage"] == "cex_recovery"]
        assert recovery[0]["filled"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["entry", "exit"])
def test_unknown_receipt_preserves_uncertainty_not_fake_flat(tmp_path, stage):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        scenario = Scenario("UNKNOWN", **{stage + "_outcome": "unknown"})
        r = simulate(p, Tape(quotes, books), scenario)
        assert r["net"] is None and r["unknown_wallet"] and r["asset_delta_raw"] is None

    asyncio.run(run())


def test_known_entry_revert_charges_bound_gas_without_cex_order(tmp_path):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        r = simulate(p, Tape(quotes, books), Scenario("REVERT", entry_outcome="revert"))
        assert r["status"] == "ABORTED_MODEL" and not r["rows"]
        assert r["net"] == pytest.approx(-p["entry_gas"] - p["safety"])

    asyncio.run(run())


def test_missing_reverse_quote_and_expiry_do_not_fabricate_exit(tmp_path):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        r = simulate(p, Tape([], books), Scenario("MISSING"))
        assert r["status"] == "RESIDUAL_MODEL" and r["net"] is None
        r = simulate(p, Tape(quotes, books), Scenario("OLD", dex_confirmation=16))
        assert r["status"] == "UNRESOLVED" and r["net"] is None and r["unknown_wallet"]

    asyncio.run(run())


def test_future_book_is_not_used_for_earlier_arrival(tmp_path):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        for b in books:
            b["book_ts"] = b["received_at"] = 1002
        tape = Tape(quotes, books)
        assert tape.book(p, 1001, 1.5) is None
        assert tape.book(p, 1002, 1.5) is not None

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["gas", "conflict", "contract", "lookahead"])
def test_stress_rejects_bad_lineage_or_retains_residual(tmp_path, fault):
    async def run():
        p, quotes, books, _ = await closed_fixture(tmp_path)
        if fault == "gas":
            quotes[0]["gas"] = 0
            with pytest.raises(ValueError, match="GAS_LINEAGE"):
                Tape(quotes, books)
        if fault == "conflict":
            b = copy.deepcopy(books[0])
            b["bids"][0][1] += 1
            books.append(b)
            with pytest.raises(ValueError, match="CONFLICT"):
                Tape(quotes, books)
        if fault == "contract":
            for b in books:
                b["contract_size"] = 0.01
            r = simulate(p, Tape([], books), Scenario("CHANGED"))
            assert r["net"] is None and r["asset_delta_raw"] != "0"
        if fault == "lookahead":
            p["entry_dex"]["received_at"] = p["opened_at"] + 1
            with pytest.raises(ValueError, match="LOOKAHEAD"):
                simulate(p, Tape(quotes, books), Scenario("BAD"))

    asyncio.run(run())


def test_database_report_persists_inputs_results_and_never_credits_ledger(tmp_path):
    async def run():
        p, _, _, history = await closed_fixture(tmp_path)
        report = await build(history.path)
        assert report["positions"] == 1 and len(report["results"]) == 6, report
        assert "модель" in render(report)
        async with aiosqlite.connect(history.path) as d:
            async with d.execute("SELECT COUNT(*) FROM dex_stress_results") as c:
                assert (await c.fetchone())[0] == 6
            async with d.execute("SELECT COUNT(*) FROM ledger") as c:
                assert (await c.fetchone())[0] == 1
        result = await readiness(
            history.path, str(tmp_path / "missing.json"), ("a", "b"), now=1001
        )
        assert (
            not result["software_complete"]
            and not result["production_ready"]
            and not result["live_allowed"]
        )
        assert (
            result["samples"]["cex_dex"]["closed"]
            == result["samples"]["cex_dex"]["eligible"]
            == 1
        )
        assert (
            "DEX_WRITE_BOOTSTRAP_AND_PAIRED_EXIT_MONITOR_NOT_CONNECTED"
            in result["missing"]
        )
        assert "runtime observer только читает" in render_readiness(result)

    asyncio.run(run())
