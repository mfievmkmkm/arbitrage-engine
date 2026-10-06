from dataclasses import dataclass
@dataclass(frozen=True)
class PositionMode:
 safe:bool
 reason:str
def check(long_mode,short_mode,required="hedge"):
 if not long_mode or not short_mode:return PositionMode(False,"POSITION_MODE_UNKNOWN")
 if long_mode!=required or short_mode!=required:return PositionMode(False,"POSITION_MODE_MISMATCH")
 return PositionMode(True,"OK")
