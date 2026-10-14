from dataclasses import dataclass,field
@dataclass
class History:
 rows:list=field(default_factory=list)
 def add(self,ts,from_venue,to_venue,amount,reason):self.rows.append({"ts":ts,"from":from_venue,"to":to_venue,"amount":amount,"reason":reason})
