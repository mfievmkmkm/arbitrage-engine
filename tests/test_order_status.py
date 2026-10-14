from app.order_status import normalize
def test_status():
 assert normalize("open",1,2)=="PARTIAL"
 assert normalize("closed",2,2)=="FILLED"
 assert normalize("weird",0,2)=="UNKNOWN"
