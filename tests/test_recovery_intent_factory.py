from app.recovery_intent_factory import entry,close
def test_recovery_ids_are_deterministic_and_close_reduce_only():
 assert entry("t","a","X","buy",1).intent_id==entry("t","a","X","buy",1).intent_id;assert close("t","a","X","sell",1).reduce_only
