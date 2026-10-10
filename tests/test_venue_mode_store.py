from app.venue_mode_store import Store
def test_venue_modes_persist(tmp_path):
 p=str(tmp_path/"v.json");s=Store(p);s.save({"binance":{"scan":True,"real":False}});assert s.load()["binance"]["scan"]
