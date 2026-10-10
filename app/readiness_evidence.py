"""Bounded, snapshot-consistent evidence inventory. Never grants authority.

Stored stress results are model observations, not independent account tests.
Missing, old or malformed evidence is explicit; no synthetic success is inferred.
"""

import hashlib
import json
import math
from collections import Counter

RUNS = {
    "futures_futures": ("execution_replay_runs", "execution_replay_results"),
    "spot_futures": ("cash_execution_runs", "cash_execution_results"),
    "spot_spot": ("cash_execution_runs", "cash_execution_results"),
    "funding_arb": ("funding_execution_runs", "funding_execution_results"),
    "cex_dex": ("dex_stress_runs", "dex_stress_results"),
}

GAP_LABELS = {
    "VERIFIED_PAPER_HISTORY_MISSING": "история Paper",
    "OOS_SPLIT_NOT_VALIDATED": "отдельная OOS-выборка",
    "OOS_MODEL_NET_NOT_POSITIVE": "NET модели OOS",
    "EXECUTION_STRESS_OLDER_THAN_24H": "свежий stress",
    "EXECUTION_STRESS_INCOMPLETE_RESULTS": "неопределённый NET stress",
    "EXECUTION_STRESS_UNRESOLVED_OUTCOMES": "остатки/UNKNOWN в stress",
    "ACCOUNT_SCOPE_NOT_CERTIFIED": "scope аккаунта",
    "WALLET_CERTIFICATION_NOT_EVALUATED": "сертификат кошелька отдельно",
    "ACTUAL_COST_CYCLE_NOT_RECORDED": "фактический цикл расходов",
    "ACTUAL_COST_EVIDENCE_INCOMPLETE": "сверка фактических расходов",
    "ACTUAL_COST_SAMPLE_CAPPED": "полная выборка расходов",
    "UNCLASSIFIED_LIVE_COST_EVIDENCE": "неопределённый scope LIVE-расходов",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def finite(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("EVIDENCE_NUMBER_INVALID")
    return value


async def stress(db, tables, strategy, now, limit=10000, max_age=86400):
    runs, results = RUNS[strategy]
    report = dict(status="HISTORY_MISSING", model_only=True, execution_authority=False)
    if runs not in tables or results not in tables:
        return report
    sql = "SELECT * FROM " + runs
    params = ()
    if runs == "cash_execution_runs":
        sql += " WHERE strategy=?"
        params = (strategy,)
    async with db.execute(sql + " ORDER BY id DESC LIMIT 1", params) as cursor:
        run = await cursor.fetchone()
    if run is None:
        return report
    report["run_id"] = run["id"]
    try:
        created = finite(run["created_at"])
        if created < 0 or created > now:
            raise ValueError("EVIDENCE_TIME_INVALID")
        payload = json.loads(run["payload"])
        if not isinstance(payload, dict):
            raise ValueError("EVIDENCE_PAYLOAD_INVALID")
        report.update(
            created_at=created,
            age_seconds=now - created,
            stale=now - created > max_age,
            source_sha256=digest(payload),
        )
        async with db.execute(
            "SELECT * FROM " + results + " WHERE run_id=? ORDER BY id LIMIT ?",
            (run["id"], limit + 1),
        ) as cursor:
            rows = await cursor.fetchall()
        if len(rows) > limit:
            raise ValueError("EVIDENCE_RESULT_LIMIT_EXCEEDED")
        statuses, scenarios, keys, positions, nets = Counter(), {}, set(), set(), []
        for row in rows:
            p = json.loads(row["payload"])
            if not isinstance(p, dict):
                raise ValueError("EVIDENCE_PAYLOAD_INVALID")
            # DEX stores its simulation directly; other runners store scenario names.
            scenario = p.get("scenario")
            if isinstance(scenario, dict):
                scenario = scenario.get("name")
            if (
                not isinstance(row["status"], str)
                or not row["status"]
                or not isinstance(scenario, str)
                or not scenario
                or scenario != row["scenario"]
                or p.get("status") != row["status"]
            ):
                raise ValueError("EVIDENCE_RESULT_CONFLICT")
            if "position_id" in p and p["position_id"] != row["position_id"]:
                raise ValueError("EVIDENCE_POSITION_CONFLICT")
            key = (row["position_id"], scenario)
            if key in keys:
                raise ValueError("EVIDENCE_DUPLICATE_RESULT")
            keys.add(key)
            positions.add(row["position_id"])
            net = p.get("net")
            if (net is None) != (row["net"] is None):
                raise ValueError("EVIDENCE_NET_CONFLICT")
            if net is not None:
                net = finite(net)
                if not math.isclose(
                    net, finite(row["net"]), rel_tol=1e-8, abs_tol=1e-8
                ):
                    raise ValueError("EVIDENCE_NET_CONFLICT")
                nets.append(net)
            statuses[row["status"]] += 1
            s = scenarios.setdefault(
                scenario, dict(results=0, completed=0, modeled_net=None, statuses={})
            )
            s["results"] += 1
            s["statuses"][row["status"]] = s["statuses"].get(row["status"], 0) + 1
            if net is not None:
                s["completed"] += 1
                s["modeled_net"] = finite((s["modeled_net"] or 0) + net)
        # Verify persisted run summary against its result journal, not just a hash.
        if runs == "dex_stress_runs":
            expected = payload.get("results")
            if not isinstance(expected, list) or len(expected) != len(rows):
                raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
            saved = {
                (x["position_id"], x["result"]["scenario"]["name"]): x["result"]
                for x in expected
            }
            if len(saved) != len(expected) or any(
                saved.get((r["position_id"], r["scenario"])) != json.loads(r["payload"])
                for r in rows
            ):
                raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
        else:
            expected = payload.get("scenarios")
            if not isinstance(expected, list) or payload.get("sample_size") != len(
                positions
            ):
                raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
            names = set()
            for item in expected:
                name = item["parameters"]["name"]
                if name in names:
                    raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
                names.add(name)
                observed = scenarios.get(
                    name, dict(statuses={}, completed=0, modeled_net=None)
                )
                if (
                    item["statuses"] != observed["statuses"]
                    or item["completed"] != observed["completed"]
                ):
                    raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
                net, other = item["net"], observed["modeled_net"]
                if (net is None) != (other is None) or (
                    net is not None
                    and not math.isclose(finite(net), other, abs_tol=1e-8)
                ):
                    raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
            if set(scenarios) - names:
                raise ValueError("EVIDENCE_SUMMARY_CONFLICT")
        report["results_sha256"] = digest([dict(r) for r in rows])
        # Do not sum across scenarios: they replay the same positions repeatedly.
        report.update(
            status="RECORDED_MODEL_ONLY" if rows else "RESULTS_MISSING",
            positions=len(positions),
            results=len(rows),
            statuses=dict(statuses),
            scenarios=scenarios,
            incomplete_results=len(rows) - len(nets),
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        report.update(
            status="EVIDENCE_INVALID",
            reason=(
                str(error)
                if isinstance(error, ValueError)
                else "EVIDENCE_PAYLOAD_INVALID"
            ),
        )
    return report


def inventory(samples, stresses, costs, account):
    rows = []
    for strategy, sample in samples.items():
        model = stresses[strategy]
        actual = [r for r in costs["trades"] if r.get("strategy") == strategy]
        reasons = []
        if not sample["eligible"]:
            reasons.append("VERIFIED_PAPER_HISTORY_MISSING")
        if sample["reason"] != "VALIDATED_SPLIT":
            reasons.append("OOS_SPLIT_NOT_VALIDATED")
        elif not sample["oos_model_positive"]:
            reasons.append("OOS_MODEL_NET_NOT_POSITIVE")
        if model["status"] != "RECORDED_MODEL_ONLY":
            reasons.append("EXECUTION_STRESS_" + model["status"])
        elif model["stale"]:
            reasons.append("EXECUTION_STRESS_OLDER_THAN_24H")
        if model.get("incomplete_results"):
            reasons.append("EXECUTION_STRESS_INCOMPLETE_RESULTS")
        if any(
            model.get("statuses", {}).get(x)
            for x in (
                "RESIDUAL_EXPOSURE",
                "EXIT_BEFORE_ENTRY_COMPLETE",
                "UNRESOLVED",
                "RESIDUAL_MODEL",
                "FUNDING_ACCOUNTING_UNKNOWN",
                "EXCLUDED",
            )
        ):
            reasons.append("EXECUTION_STRESS_UNRESOLVED_OUTCOMES")
        if strategy == "cex_dex":
            reasons.append("WALLET_CERTIFICATION_NOT_EVALUATED")
        elif not account.get(strategy):
            reasons.append("ACCOUNT_SCOPE_NOT_CERTIFIED")
        if not actual:
            reasons.append("ACTUAL_COST_CYCLE_NOT_RECORDED")
        elif any(r["status"] != "RECONCILED" for r in actual):
            reasons.append("ACTUAL_COST_EVIDENCE_INCOMPLETE")
        if costs.get("capped"):
            reasons.append("ACTUAL_COST_SAMPLE_CAPPED")
        if any(r.get("strategy") not in RUNS for r in costs["trades"]):
            reasons.append("UNCLASSIFIED_LIVE_COST_EVIDENCE")
        rows.append(
            dict(
                strategy=strategy,
                eligible_paper=sample["eligible"],
                oos_status=sample["reason"],
                oos_model_positive=sample["oos_model_positive"],
                stress_status=model["status"],
                stress_run_id=model.get("run_id"),
                actual_cycles_in_sample=len(actual),
                reconciled_cycles_in_sample=sum(
                    r["status"] == "RECONCILED" for r in actual
                ),
                diagnostic_gaps=reasons,
                execution_authority=False,
            )
        )
    return rows
