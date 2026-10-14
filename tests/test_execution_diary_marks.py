from app.execution_diary import ExecutionEvent
def test_mark_payload():
 r=ExecutionEvent("t","MARK",symbol="X",net=1.2,spread=.5).row()
 assert r["net"]==1.2 and r["spread"]==.5
