from dataclasses import dataclass
@dataclass(frozen=True)
class Alert:
 severity:str
 text:str
def build(reason,trade_id="",symbol=""):
 r=str(reason);u=r.upper()
 sev="🚨 CRITICAL" if any(x in u for x in ("UNKNOWN","UNTRUSTED","FLIPPED","MISMATCH","CIRCUIT")) else "⚠️ WARNING"
 ctx=" • ".join(x for x in (trade_id,symbol) if x)
 return Alert(sev,f"{sev}\n{r}"+(f"\n{ctx}" if ctx else ""))
