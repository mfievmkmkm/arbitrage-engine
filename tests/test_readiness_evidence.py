import asyncio
import json
import sqlite3
import zipfile

import aiosqlite
import pytest

from app.project_readiness import build, read, render, json_metrics
from app.readiness_evidence import RUNS, digest, inventory, stress


def fixture(path, strategy="futures_futures", *, created=100, net=0.3):
    runs, results = RUNS[strategy]
    p = dict(position_id=1, scenario="BASELINE", status="COMPLETE", net=net)
    if strategy == "cex_dex":
        p["scenario"] = dict(name="BASELINE")
        summary = dict(positions=1, results=[dict(position_id=1, result=p)])
    else:
        summary = dict(
            sample_size=1,
            scenarios=[
                dict(
                    parameters=dict(name="BASELINE"),
                    statuses=dict(COMPLETE=1),
                    completed=int(net is not None),
                    net=net,
                )
            ],
        )
    with sqlite3.connect(path) as d:
        d.execute(
            f"CREATE TABLE {runs}(id INTEGER PRIMARY KEY,created_at REAL,payload TEXT"
            + (",strategy TEXT" if runs == "cash_execution_runs" else "")
            + ")"
        )
        d.execute(
            f"CREATE TABLE {results}(id INTEGER PRIMARY KEY,run_id INTEGER,position_id INTEGER,scenario TEXT,status TEXT,net REAL,payload TEXT)"
        )
        if runs == "cash_execution_runs":
            d.execute(
                f"INSERT INTO {runs} VALUES(1,?,?,?)",
                (created, json.dumps(summary), strategy),
            )
        else:
            d.execute(
                f"INSERT INTO {runs} VALUES(1,?,?)", (created, json.dumps(summary))
            )
        d.execute(
            f"INSERT INTO {results} VALUES(1,1,1,'BASELINE','COMPLETE',?,?)",
            (net, json.dumps(p)),
        )
    return summary, p


async def load(path, strategy="futures_futures", now=101, **kwargs):
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as c:
            tables = {r[0] for r in await c.fetchall()}
        return await stress(db, tables, strategy, now, **kwargs)


@pytest.mark.parametrize("strategy", RUNS)
def test_all_five_stress_readers_never_authorize_or_mutate(tmp_path, strategy):
    path = tmp_path / "evidence.db"
    fixture(path, strategy)
    before = path.read_bytes()
    r = asyncio.run(load(path, strategy))
    assert r["status"] == "RECORDED_MODEL_ONLY", r
    assert r["positions"] == r["results"] == 1
    assert r["scenarios"]["BASELINE"]["modeled_net"] == 0.3
    assert not r["execution_authority"] and r["model_only"]
    assert len(r["source_sha256"]) == len(r["results_sha256"]) == 64
    assert "net" not in r  # repeated scenarios are not added together
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("UPDATE execution_replay_results SET net=0.4", "EVIDENCE_NET_CONFLICT"),
        (
            "UPDATE execution_replay_results SET scenario='FAST'",
            "EVIDENCE_RESULT_CONFLICT",
        ),
        (
            "UPDATE execution_replay_results SET status='FAILED'",
            "EVIDENCE_RESULT_CONFLICT",
        ),
        (
            "UPDATE execution_replay_results SET position_id=2",
            "EVIDENCE_POSITION_CONFLICT",
        ),
        (
            "UPDATE execution_replay_results SET payload='[]'",
            "EVIDENCE_PAYLOAD_INVALID",
        ),
        ("UPDATE execution_replay_runs SET payload='[]'", "EVIDENCE_PAYLOAD_INVALID"),
        ("UPDATE execution_replay_runs SET payload='{}'", "EVIDENCE_SUMMARY_CONFLICT"),
        ("DELETE FROM execution_replay_results", "EVIDENCE_SUMMARY_CONFLICT"),
        (
            "INSERT INTO execution_replay_results SELECT 2,run_id,position_id,scenario,status,net,payload FROM execution_replay_results",
            "EVIDENCE_DUPLICATE_RESULT",
        ),
        ("UPDATE execution_replay_runs SET created_at=102", "EVIDENCE_TIME_INVALID"),
    ],
)
def test_conflicting_evidence_is_not_counted_as_valid(tmp_path, mutation, reason):
    path = tmp_path / "evidence.db"
    fixture(path)
    with sqlite3.connect(path) as d:
        d.execute(mutation)
    r = asyncio.run(load(path))
    assert r["status"] == "EVIDENCE_INVALID" and r["reason"] == reason
    assert not r["execution_authority"]


@pytest.mark.parametrize("strategy", RUNS)
def test_run_summary_cannot_hide_deleted_or_changed_result(tmp_path, strategy):
    path = tmp_path / "evidence.db"
    fixture(path, strategy)
    with sqlite3.connect(path) as d:
        d.execute(f"DELETE FROM {RUNS[strategy][1]}")
    assert asyncio.run(load(path, strategy))["status"] == "EVIDENCE_INVALID"


def test_unknown_net_is_explicit_and_staleness_is_diagnostic(tmp_path):
    path = tmp_path / "evidence.db"
    fixture(path, net=None)
    r = asyncio.run(load(path, now=100 + 86401))
    assert r["stale"] and r["incomplete_results"] == 1
    assert r["scenarios"]["BASELINE"]["modeled_net"] is None
    assert not r["execution_authority"]


def test_capped_results_are_not_a_partial_success(tmp_path):
    path = tmp_path / "evidence.db"
    fixture(path)
    r = asyncio.run(load(path, limit=0))
    assert r["reason"] == "EVIDENCE_RESULT_LIMIT_EXCEEDED"


def test_cash_runs_are_scoped_not_latest_other_strategy(tmp_path):
    path = tmp_path / "evidence.db"
    summary, _ = fixture(path, "spot_futures")
    with sqlite3.connect(path) as d:
        d.execute(
            "INSERT INTO cash_execution_runs VALUES(2,100,?,'spot_spot')",
            (json.dumps(summary),),
        )
    assert asyncio.run(load(path, "spot_futures"))["run_id"] == 1
    assert asyncio.run(load(path, "spot_spot"))["status"] == "EVIDENCE_INVALID"


def test_missing_database_never_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        asyncio.run(build(path, tmp_path / "acceptance.json", now=101))
    assert not path.exists()


def test_readiness_snapshot_hash_and_diagnostics_on_empty_database(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    r = asyncio.run(build(path, tmp_path / "missing.json", now=101))
    proof = r.pop("evidence_sha256")
    assert proof == digest(r)
    r["evidence_sha256"] = proof
    assert len(r["evidence_inventory"]) == 5
    assert not r["live_allowed"] and not r["production_ready"]
    assert all(
        "OOS_SPLIT_NOT_VALIDATED" in x["diagnostic_gaps"]
        for x in r["evidence_inventory"]
    )
    assert "одна транзакция" in render(r)
    assert "NET разных stress" in render(r)


def test_existing_read_transaction_sees_one_snapshot_despite_writer(tmp_path):
    async def run():
        path = tmp_path / "wal.db"
        fixture(path)
        with sqlite3.connect(path) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
        async with aiosqlite.connect(path) as db:
            await db.execute("BEGIN")
            await db.execute("SELECT * FROM execution_replay_runs")
            with sqlite3.connect(path) as writer:
                writer.execute("UPDATE execution_replay_results SET net=99")
            r = await read(db, 101)
            assert (
                r["execution_stress"]["futures_futures"]["status"]
                == "RECORDED_MODEL_ONLY"
            )
        assert (await load(path))["status"] == "EVIDENCE_INVALID"

    asyncio.run(run())


def test_infinite_no_loss_profit_factor_is_json_safe_without_fabricating_value():
    result = json_metrics(dict(net=0.4, profit_factor=float("inf")))
    assert result["profit_factor"] is None and result["profit_factor_unbounded"]
    json.dumps(result, allow_nan=False)


def test_inventory_does_not_promote_positive_models_or_reconciled_costs():
    samples = dict(
        futures_futures=dict(
            eligible=1000, reason="VALIDATED_SPLIT", oos_model_positive=True
        )
    )
    stresses = dict(
        futures_futures=dict(
            status="RECORDED_MODEL_ONLY", stale=False, incomplete_results=0
        )
    )
    costs = dict(
        trades=[dict(strategy="futures_futures", status="RECONCILED")], capped=True
    )
    r = inventory(samples, stresses, costs, {})[0]
    assert "ACCOUNT_SCOPE_NOT_CERTIFIED" in r["diagnostic_gaps"]
    assert "ACTUAL_COST_SAMPLE_CAPPED" in r["diagnostic_gaps"]
    assert not r["execution_authority"]


def test_export_includes_model_evidence_in_same_audit(tmp_path):
    from app.audit_export import build as export

    path = tmp_path / "evidence.db"
    fixture(path)
    _, archive = asyncio.run(export(path, tmp_path / "exports"))
    with zipfile.ZipFile(archive) as z:
        assert "paper_oos_evidence.csv" in z.namelist()
        assert "stress_evidence.csv" in z.namelist()
        assert "RECORDED_MODEL_ONLY" in z.read("stress_evidence.csv").decode()


def test_cli_json_read_only_without_trading_runtime(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "evidence.db"
    fixture(path)
    result = subprocess.run(
        [sys.executable, "-m", "app.project_readiness", "--db", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert not report["production_ready"] and len(report["evidence_sha256"]) == 64


def test_cli_missing_db_fails_without_creating_it(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "missing.db"
    result = subprocess.run(
        [sys.executable, "-m", "app.project_readiness", "--db", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "READINESS_DATABASE_MISSING" in result.stderr
    assert not path.exists()


def test_wallet_acceptance_not_claimed_by_cex_account_report():
    sample = dict(eligible=10, reason="VALIDATED_SPLIT", oos_model_positive=True)
    model = dict(status="RECORDED_MODEL_ONLY", stale=False, statuses=dict(UNRESOLVED=1))
    rows = inventory(
        dict(cex_dex=sample),
        dict(cex_dex=model),
        dict(trades=[], capped=False),
        dict(cex_dex=True),
    )
    assert "WALLET_CERTIFICATION_NOT_EVALUATED" in rows[0]["diagnostic_gaps"]
    assert "EXECUTION_STRESS_UNRESOLVED_OUTCOMES" in rows[0]["diagnostic_gaps"]


def test_readiness_screen_bounded_and_localized(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    r = asyncio.run(build(path, tmp_path / "missing.json", now=101))
    text = render(r)
    assert len(text) < 4096
    assert "Проверить: история Paper" in text


@pytest.mark.parametrize("strategy", ["spot_futures", "spot_spot", "funding_arb"])
def test_real_persisted_runner_summaries_match_journal(tmp_path, strategy):
    async def run():
        path = tmp_path / "model.db"
        if strategy == "funding_arb":
            from tests.test_funding_execution_replay import seed
            from app.funding_execution_replay import build as model

            seed(path)
            await model(path, clock=lambda: 1060)
        else:
            from tests.test_cash_execution_replay import seed
            from app.cash_execution_replay import build as model

            seed(path, strategy)
            await model(path, strategy, clock=lambda: 120)
        result = await load(path, strategy, now=1061)
        assert result["status"] == "RECORDED_MODEL_ONLY", result
        assert len(result["scenarios"]) == 3

    asyncio.run(run())


def test_real_dex_stress_journal_matches_nested_run(tmp_path):
    async def run():
        from tests.test_dex_execution_stress import closed_fixture
        from app.dex_execution_stress import build as model

        _, _, _, history = await closed_fixture(tmp_path)
        await model(history.path, clock=lambda: 1001)
        r = await load(history.path, "cex_dex", now=1002)
        assert r["status"] == "RECORDED_MODEL_ONLY", r
        assert len(r["scenarios"]) == 6

    asyncio.run(run())


def test_oos_bounded_history_is_explicit_not_silently_sampled(tmp_path):
    async def run():
        from tests.test_spot_future_history_replay import history
        from app.spot_future_history_replay import dataset

        path = tmp_path / "paper.db"
        await history(path)
        rows, excluded = await dataset(path, limit=0)
        assert not rows and excluded == dict(HISTORY_LIMIT_EXCEEDED=1)
        rows, excluded = await dataset(path, mark_limit=1)
        assert not rows and excluded == dict(MARK_LIMIT_EXCEEDED=1)

    asyncio.run(run())


def test_held_cash_inventory_not_hidden_by_closed_live_count(tmp_path):
    from app.spot_future_live_result import SCHEMA

    path = tmp_path / "inventory.db"
    with sqlite3.connect(path) as d:
        d.executescript(SCHEMA)
        d.execute(
            "INSERT INTO live_cash_inventory VALUES('t','a','X',0.001,0.01,100,'{}')"
        )
    report = asyncio.run(build(path, tmp_path / "missing.json", now=101))
    assert report["active_live"] == 0 and report["held_inventory_records"] == 1
    assert "не private-flat" in render(report)


def test_unknown_cost_strategy_cannot_disappear_from_diagnostics():
    row = inventory(
        dict(
            futures_futures=dict(
                eligible=0, reason="HISTORY_MISSING", oos_model_positive=False
            )
        ),
        dict(futures_futures=dict(status="HISTORY_MISSING")),
        dict(trades=[dict(status="PARTIAL")]),
        {},
    )[0]
    assert "UNCLASSIFIED_LIVE_COST_EVIDENCE" in row["diagnostic_gaps"]
