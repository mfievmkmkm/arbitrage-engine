from app.net_edge_proof import prove
def test_net_edge_proof_is_full_roundtrip():assert not prove(.5,.1,.1,0,.1,.1,.2,.1)["allowed"]
