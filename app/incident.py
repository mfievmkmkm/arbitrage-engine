from dataclasses import dataclass
@dataclass(frozen=True)
class Incident:
 code:str
 severity:str
 action:str
def classify(private_stream_ok=True,position_trusted=True,one_leg_only=False,repeated_errors=False):
 if not position_trusted:return Incident("STATE_UNKNOWN","CRITICAL","HALT_AND_RECONCILE")
 if not private_stream_ok:return Incident("PRIVATE_STREAM_LOST","CRITICAL","NO_NEW_TRADES")
 if one_leg_only:return Incident("ONE_LEG_FILLED","HIGH","RECOVER_OR_FLATTEN")
 if repeated_errors:return Incident("REPEATED_ERRORS","HIGH","HALT")
 return Incident("OK","INFO","CONTINUE")
