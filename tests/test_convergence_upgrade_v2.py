"""One-shot repair for persisted disabled PAPER convergence routes."""
from types import SimpleNamespace

from app.paired_paper_profile import migrate_toggles


class Store:
    def __init__(self):
        self.snapshots = []
    def save(self, enabled):
        self.snapshots.append(dict(enabled))


def test_reenable_agreed_paper_routes_once_and_preserve_explicit_later_disable(tmp_path):
    path = tmp_path / "runtime_state.json"
    legacy_marker = tmp_path / "runtime_state.json.paired_migration.json"
    legacy_marker.write_text('{"version": 1}', encoding="utf-8")
    runtime = SimpleNamespace(enabled={
        "futures_futures": False,
        "spot_futures": False,
        "spot_spot": True,
        "funding_arb": False,
        "dex_paired": True,
        "product_dex_paired": True,
        "dex_channel": True,
        "cex_dex": True,
    })
    store = Store()
    migrate_toggles(path, runtime, store)
    assert runtime.enabled["futures_futures"] is True
    assert runtime.enabled["spot_futures"] is True
    assert runtime.enabled["spot_spot"] is True  # Existing mode not force-modified here
    assert runtime.enabled["dex_channel"] is False
    assert runtime.enabled["cex_dex"] is False
    assert runtime.enabled["product_dex_paired"] is True
    assert (tmp_path / "runtime_state.json.convergence_v2.json").exists()
    runtime.enabled["futures_futures"] = False
    runtime.enabled["spot_futures"] = False
    migrate_toggles(path, runtime, store)
    assert runtime.enabled["futures_futures"] is False
    assert runtime.enabled["spot_futures"] is False
    assert store.snapshots[-1]["futures_futures"] is False


def test_fresh_migration_still_does_not_activate_live(tmp_path):
    path = tmp_path / "runtime_state.json"
    runtime = SimpleNamespace(enabled={"futures_futures": False, "spot_futures": False})
    store = Store()
    migrate_toggles(path, runtime, store)
    assert runtime.enabled["futures_futures"]
    assert runtime.enabled["spot_futures"]
    assert runtime.enabled["dex_channel"] is False
    assert runtime.enabled["cex_dex"] is False
    assert (tmp_path / "runtime_state.json.paired_migration.json").exists()
