from dataclasses import dataclass
@dataclass(frozen=True)
class DuplicateOpportunity:
 allowed:bool
 reason:str
def check(symbol,long_venue,short_venue,trades):
 for t in trades:
  if t.symbol==symbol and {t.long_venue,t.short_venue}=={long_venue,short_venue}:return DuplicateOpportunity(False,"DUPLICATE_PAIR_EXPOSURE")
 return DuplicateOpportunity(True,"OK")
