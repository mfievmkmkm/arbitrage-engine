def render(ledger,allocations=None):
 return "💰 CAPITAL\nEquity: $%.2f\nRealized: %+.2f\nFees: -%.2f\nFunding: %+.2f\nSlippage: -%.2f\nAllocations: %s"%(ledger.equity,ledger.realized,ledger.fees,ledger.funding,ledger.slippage,allocations or {})
