from dataclasses import dataclass
@dataclass(frozen=True)
class VerifiedFee:
 verified:bool;maker:float|None;taker:float|None;source:str

def from_trading_fee(row):
 try:
  m=float(row["maker"]);t=float(row["taker"])
  return VerifiedFee(m>=0 and t>=0,m,t,"account")
 except Exception:return VerifiedFee(False,None,None,"unknown")
