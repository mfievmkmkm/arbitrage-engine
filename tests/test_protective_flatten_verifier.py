from app.protective_flatten_verifier import result
def test_flatten_requires_private_zero():
 assert result(True,None)=="FLATTEN_UNVERIFIED";assert result(True,{"long":1})=="FLATTEN_UNVERIFIED";assert result(True,{"long":0,"short":0})=="FLATTENED_PRIVATE_VERIFIED"
