from dataclasses import dataclass
@dataclass(frozen=True)
class Authority:safe:bool;reason:str;rows:tuple
def evaluate(rows,private_trusted,unknown_intents):
 active=tuple(x for x in rows if x.get("phase") not in ("CLOSED_PRIVATE_VERIFIED","CLOSED_WITH_INVENTORY","ABORTED"))
 if unknown_intents:return Authority(False,"UNKNOWN_ORDER_INTENTS",active)
 if active and not private_trusted:return Authority(False,"ACTIVE_DB_PRIVATE_UNTRUSTED",active)
 return Authority(True,"OK",active)
