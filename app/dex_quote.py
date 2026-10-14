from dataclasses import dataclass
@dataclass(frozen=True)
class DexQuote:
 chain:str;token_in:str;token_out:str;amount_in:float;amount_out:float;gas_usd:float;price_impact_pct:float;route:str
 @property
 def valid(self):return self.amount_in>0 and self.amount_out>0 and self.gas_usd>=0 and self.price_impact_pct>=0
