from app.startup_reconcile import evaluate_snapshot,all_ready
class H:
 def __init__(self,ok):self.ok=ok
def test_multi_venue():
 rows=evaluate_snapshot({"a":{"health":H(True),"positions":[],"orders":[]},"b":{"health":H(False),"positions":[],"orders":[]}})
 assert not all_ready(rows)
 assert rows[1].action=="PRIVATE_API_UNAVAILABLE"
