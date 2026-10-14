from app.export_service import write_csv
def test_export_service_writes_csv(tmp_path):
 p=write_csv(tmp_path/"x.csv",[{"a":1,"b":{"x":2}}]);assert "a,b" in open(p).read()
