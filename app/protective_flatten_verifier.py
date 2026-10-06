def result(submit_ok,private_snapshot,expected_zero=True):
 if not submit_ok:return "FLATTEN_SUBMIT_FAILED"
 if private_snapshot is None:return "FLATTEN_UNVERIFIED"
 if expected_zero and any(abs(float(x))>1e-10 for x in private_snapshot.values()):return "FLATTEN_UNVERIFIED"
 return "FLATTENED_PRIVATE_VERIFIED"
