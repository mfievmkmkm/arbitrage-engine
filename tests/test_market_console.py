from app.market_console import merged
class R:latest={"a":[{"net":1}],"b":[{"net":3}]}
def test_market_console_merges_by_edge():assert merged(R())[0][0]==3
