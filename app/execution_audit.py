from dataclasses import dataclass
@dataclass(frozen=True)
class Audit:
 ok:bool;issues:list
def trade(entry,exit_result=None):
 issues=[]
 if getattr(entry,"long_error",""):issues.append("LONG_ENTRY_ERROR")
 if getattr(entry,"short_error",""):issues.append("SHORT_ENTRY_ERROR")
 if not getattr(entry,"hedged",False):issues.append("ENTRY_NOT_HEDGED")
 if exit_result is not None:
  if getattr(exit_result,"long_error",""):issues.append("LONG_EXIT_ERROR")
  if getattr(exit_result,"short_error",""):issues.append("SHORT_EXIT_ERROR")
  if not getattr(exit_result,"flat",False):issues.append("EXIT_NOT_FLAT")
 return Audit(not issues,issues)
