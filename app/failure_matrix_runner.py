from dataclasses import dataclass
from .failure_matrix import cases
@dataclass(frozen=True)
class MatrixReport:
 passed:bool
 total:int
 failures:tuple
def evaluate(observed):
 failures=[]
 for c in cases():
  got=observed.get(c.name)
  if got is None or c.expected not in str(got):failures.append(c.name)
 return MatrixReport(not failures,len(cases()),tuple(failures))
