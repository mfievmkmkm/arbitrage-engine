from dataclasses import dataclass
@dataclass
class StopController:
 stopped:bool=False
 reason:str=""
 def stop(self,reason="OPERATOR_STOP"):
  self.stopped=True;self.reason=reason
 def resume(self):
  self.stopped=False;self.reason=""
 def allow_new_entries(self):return not self.stopped
