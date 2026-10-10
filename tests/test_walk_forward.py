import asyncio
import copy
import json
import sqlite3

import pytest

from app.walk_forward import evaluate, build, render, STRATEGIES


def trades(n=50, strategy="spot_futures"):
    result = []
    for i in range(n):
        opened = 1000 + i * 1000
        marks = [(opened + 60, 0.1), (opened + 120, 0.2), (opened + 300, 0.3)]
        row = dict(id=i + 1, opened_at=opened, closed_at=opened + 300, marks=marks)
        if strategy == "futures_futures":
            row.update(entry_spread=10, marks=[(ts, net, 1) for ts, net in marks])
        result.append(row)
    return result


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_all_strategies_multiple_disjoint_folds_never_authorize(strategy):
    r = evaluate(trades(strategy=strategy), strategy=strategy)
    assert r["status"] == "MULTI_FOLD_VALIDATED_MODEL" and len(r["folds"]) == 3
    assert r["model_positive"]
    ids = [i for f in r["folds"] for i in f["test_ids"]]
    assert len(ids) == len(set(ids)) == r["completed_positions"] == 30
    assert not r["execution_authority"] and not r["release_authorized"]
    json.dumps(r, allow_nan=False)


def test_future_test_marks_cannot_change_first_fold_selected_rules():
    data = trades()
    first = evaluate(data)
    changed = copy.deepcopy(data)
    for r in changed[20:]:
        r["marks"] = [(ts, -100) for ts, net in r["marks"]]
    second = evaluate(changed)
    assert second["folds"][0]["parameters"] == first["folds"][0]["parameters"]
    assert second["folds"][0]["train_metrics"] == first["folds"][0]["train_metrics"]
    assert not second["model_positive"]


def test_overlap_purge_uses_original_close_not_best_simulated_exit():
    data = trades()
    data[19]["closed_at"] = data[20]["opened_at"] + 1
    r = evaluate(data)
    assert r["folds"][0]["purged"] == 1 and r["folds"][0]["train_size"] == 19


def test_same_timestamp_group_never_split_between_folds():
    data = trades(52)
    data[30]["opened_at"] = data[29]["opened_at"]
    r = evaluate(data)
    assert {30, 31} <= set(r["folds"][0]["test_ids"])
    assert not ({30, 31} & set(r["folds"][1]["test_ids"]))


def test_censored_test_or_training_is_not_validated():
    data = trades()
    for r in data[20:30]:
        r["marks"] = [(r["opened_at"] + 1, -0.1)]
    r = evaluate(data)
    assert r["status"] == "PARTIAL_FOLDS" and not r["model_positive"]
    assert r["folds"][0]["censored"] == 10
    assert r["completed_positions"] < r["tested_positions"]


def test_cap_and_single_fold_are_explicitly_not_multifold_success():
    r = evaluate(trades(), max_folds=1)
    assert r["capped"] and not r["model_positive"] and r["status"] == "PARTIAL_FOLDS"
    r = evaluate(trades(30))
    assert len(r["folds"]) == 1 and r["status"] == "PARTIAL_FOLDS"


@pytest.mark.parametrize(
    "settings",
    [
        dict(test_size=0),
        dict(initial_train=True),
        dict(min_train=21),
        dict(max_folds=101),
        dict(strategy="other"),
    ],
)
def test_invalid_settings_rejected(settings):
    with pytest.raises(ValueError):
        evaluate(trades(), **settings)


def test_duplicate_positions_rejected_not_double_counted():
    data = trades()
    data[1]["id"] = data[0]["id"]
    with pytest.raises(ValueError, match="DUPLICATE"):
        evaluate(data)


def test_empty_database_readonly_and_telegram_screen(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    before = path.read_bytes()
    report = asyncio.run(build(path))
    assert set(report["strategies"]) == set(STRATEGIES)
    assert all(
        x["status"] == "INSUFFICIENT_DATA" for x in report["strategies"].values()
    )
    assert path.read_bytes() == before
    assert len(render(report)) < 4096


def test_missing_database_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        asyncio.run(build(path))
    assert not path.exists()


def test_telegram_runtime_route_and_primary_button(tmp_path, monkeypatch):
    import app.main as main
    from app.tg_ui import replay_menu

    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    from types import SimpleNamespace

    monkeypatch.setattr(main, "config", SimpleNamespace(db_path=path, interval=30))
    text = asyncio.run(main.text_for("walk_forward"))
    assert "Walk-forward" in text
    assert any(
        b.callback_data == "walk_forward" and b.style == "primary"
        for row in replay_menu("walk_forward").inline_keyboard
        for b in row
    )


def test_cpu_research_runs_off_telegram_monitor_event_loop(tmp_path, monkeypatch):
    import app.walk_forward as module
    import threading

    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    caller = threading.get_ident()
    threads = []
    original = module.evaluate

    def instrumented(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "evaluate", instrumented)
    asyncio.run(build(path))
    assert len(threads) == 5 and all(t != caller for t in threads)


def test_total_mark_cap_returns_no_partial_successful_sample(tmp_path):
    async def run():
        from tests.test_spot_future_history_replay import history
        from app.spot_future_history_replay import dataset

        path = tmp_path / "history.db"
        await history(path)
        rows, excluded = await dataset(path, total_mark_limit=1)
        assert not rows and excluded == dict(TOTAL_MARK_LIMIT_EXCEEDED=1)

    asyncio.run(run())
