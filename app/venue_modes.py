from dataclasses import dataclass
@dataclass
class Modes:
 scan:bool=True;paper:bool=True;real:bool=False
 def enable_real(self,certified,acceptance):
  self.real=bool(certified and acceptance);return self.real
