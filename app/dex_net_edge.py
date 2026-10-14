from dataclasses import dataclass
@dataclass(frozen=True)
class DexNet:
 net_usd:float;allowed:bool

def calculate(gross_usd,cex_fee_usd,dex_fee_usd,gas_usd,slippage_usd,safety_usd):
 n=float(gross_usd)-sum(map(float,(cex_fee_usd,dex_fee_usd,gas_usd,slippage_usd,safety_usd)))
 return DexNet(n,n>0)
