from app.restart_live_authority import evaluate
def test_active_db_trade_requires_private_trust():assert not evaluate([{"phase":"OPEN"}],False,{}).safe
def test_unknown_intents_block_restart():assert not evaluate([],True,{"x":"UNKNOWN"}).safe
