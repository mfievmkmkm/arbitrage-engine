from dataclasses import dataclass
@dataclass(frozen=True)
class Change:
 allowed:bool;reason:str

def check(old_version,new_version,replay_validated,operator_approved):
 if old_version==new_version:return Change(True,"UNCHANGED")
 if not replay_validated:return Change(False,"REPLAY_REQUIRED")
 if not operator_approved:return Change(False,"APPROVAL_REQUIRED")
 return Change(True,"PROMOTED")
