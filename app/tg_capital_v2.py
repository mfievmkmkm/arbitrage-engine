def render(route):
 out=["<b>CAPITAL // ROUTER</b>","<code>CONSERVATIVE ALLOCATION</code>","",f"Equity    <b>{route['equity']:.2f} USDT</b>",f"Reserve   <b>{route['reserve']:.2f} USDT</b>",""]
 for k,v in route["allocated"].items():out.append(f"{k:<18} <b>{v:.2f}</b>")
 out+=["",f"Preferred venues  <code>{', '.join(route['venues']) or '—'}</code>","<i>Rebalancing is recommendation-only. Withdrawal API is not used.</i>"];return "\n".join(out)
