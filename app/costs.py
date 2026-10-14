from dataclasses import dataclass
@dataclass(frozen=True)
class CostBreakdown:
    entry_fees_usd:float
    exit_fees_usd:float
    funding_usd:float=0.0
    slippage_buffer_usd:float=0.0
    @property
    def total_usd(self):return self.entry_fees_usd+self.exit_fees_usd+self.funding_usd+self.slippage_buffer_usd
def taker_round_trip(notional,buy_bps,sell_bps):
    one_way=notional*(buy_bps+sell_bps)/10000
    return CostBreakdown(one_way,one_way)
def net_edge_usd(gross_usd,costs):return gross_usd-costs.total_usd
