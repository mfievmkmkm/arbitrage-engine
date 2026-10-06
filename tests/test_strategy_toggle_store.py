from app.strategy_toggle_store import Store
def test_strategy_toggles_persist(tmp_path):
 p=str(tmp_path/"s.json");s=Store(p);s.save({"spot_futures":False});assert not s.load({"spot_futures":True})["spot_futures"]
