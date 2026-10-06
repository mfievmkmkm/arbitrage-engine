from dataclasses import dataclass
@dataclass
class Ledger:
 starting:float;realized:float=0;fees:float=0;funding:float=0;slippage:float=0
 @property
 def equity(self):return self.starting+self.realized-self.fees+self.funding-self.slippage
 def apply(self,pnl,fees=0,funding=0,slippage=0):self.realized+=pnl;self.fees+=fees;self.funding+=funding;self.slippage+=slippage;return self.equity
