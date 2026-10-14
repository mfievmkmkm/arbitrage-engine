from app.venue_cert_store import Store
def test_cert_store_persists(tmp_path):
 s=Store(str(tmp_path/"c.json"));s.save({"a":{"passed":True}});assert s.load()["a"]["passed"]
