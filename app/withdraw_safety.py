from dataclasses import dataclass
@dataclass(frozen=True)
class WithdrawSafety:
 safe:bool
 reason:str
def verify(explicit_no_withdraw_permission):
 return WithdrawSafety(bool(explicit_no_withdraw_permission),"VERIFIED" if explicit_no_withdraw_permission else "WITHDRAW_PERMISSION_NOT_ATTESTED")
