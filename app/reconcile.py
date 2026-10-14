from dataclasses import dataclass
@dataclass(frozen=True)
class Position:
    symbol:str
    side:str
    qty:float
@dataclass(frozen=True)
class ReconcileResult:
    trusted:bool
    reason:str
    exposure:dict
def reconcile(positions,expected=None,tolerance=1e-8):
    exposure={}
    for p in positions:
        signed=p.qty if p.side.lower() in ("long","buy") else -p.qty
        exposure[p.symbol]=exposure.get(p.symbol,0.0)+signed
    unknown={s:q for s,q in exposure.items() if abs(q)>tolerance}
    if expected is None:
        return ReconcileResult(not unknown,"FLAT" if not unknown else "UNEXPECTED_EXPOSURE",exposure)
    for symbol in set(exposure)|set(expected):
        if abs(exposure.get(symbol,0.0)-expected.get(symbol,0.0))>tolerance:return ReconcileResult(False,"POSITION_MISMATCH",exposure)
    return ReconcileResult(True,"MATCH",exposure)
