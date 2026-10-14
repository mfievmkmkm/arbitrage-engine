from dataclasses import dataclass
@dataclass(frozen=True)
class CorrelationGuard:
 allowed:bool;reason:str

def check(open_bases,new_base,max_same_base=1):
 n=sum(1 for x in open_bases if x==new_base)
 return CorrelationGuard(n<max_same_base,"OK" if n<max_same_base else "SAME_BASE_CONCENTRATION")
