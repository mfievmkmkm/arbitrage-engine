def render(summary):return "📈 ANALYTICS\nTrades: %s\nNET: %+.4f$\nPF: %s\nMax DD: %s"%(summary.get("trades",0),summary.get("net",0),summary.get("profit_factor","—"),summary.get("max_drawdown","—"))
