from app.withdraw_safety import verify
def test_withdraw_permission_requires_explicit_attestation():
 assert verify(True).safe and not verify(False).safe
