from dataclasses import dataclass
@dataclass(frozen=True)
class ReleaseManifest:
 version:str;commit:str;venues:tuple;strategies:tuple;max_notional:float;max_leverage:float

def validate(m):
 return bool(m.version and m.commit and len(m.venues)>=2 and m.strategies and m.max_notional>0 and m.max_leverage>0)
