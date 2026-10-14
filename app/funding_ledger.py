from dataclasses import dataclass,field
@dataclass
class FundingLedger:
 rows:list=field(default_factory=list)
 def add(self,trade_id,venue,amount,ts):self.rows.append({"trade_id":trade_id,"venue":venue,"amount":amount,"ts":ts})
 def total(self):return sum(x["amount"] for x in self.rows)
