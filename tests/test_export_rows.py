from app.export_rows import csv_text
def test_csv_export_serializes_payload():
 x=csv_text([{"a":1,"payload":{"x":2}}]);assert "payload" in x and "x" in x
