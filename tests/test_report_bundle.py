from app.report_bundle import build
def test_report_bundle_builds_audit_csvs(tmp_path):
 x=build(tmp_path,[{"a":1}],[],[],[]);assert set(x)=={"observations","trades","ledger","replay"}
