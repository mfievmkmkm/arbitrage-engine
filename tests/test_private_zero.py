from app.private_zero import verified
def test_private_zero_requires_every_venue():assert verified({"a":0,"b":0},"X",["a","b"]) and not verified({"a":0},"X",["a","b"])
