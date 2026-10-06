from dataclasses import dataclass
@dataclass(frozen=True)
class Candidate:
 allowed:bool;reasons:tuple

def check(op,min_edge,books_fresh,fee_verified,funding_known):
 reasons=[]
 if op is None:reasons.append("NO_QUOTE")
 elif op["hypothetical_edge"]<min_edge:reasons.append("NET_EDGE")
 if not books_fresh:reasons.append("STALE_BOOK")
 if not fee_verified:reasons.append("FEE_UNVERIFIED")
 if not funding_known:reasons.append("FUNDING_UNKNOWN")
 return Candidate(not reasons,tuple(reasons))
