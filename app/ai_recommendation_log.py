from dataclasses import dataclass,field
@dataclass
class Log:
 rows:list=field(default_factory=list)
 def add(self,ts,text,param_version,replay_validated=False,approved=False):self.rows.append({"ts":ts,"text":text,"version":param_version,"replay_validated":replay_validated,"approved":approved})
