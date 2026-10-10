def render(equity,allocations,free,exposure):
 out=["<b>CAPITAL ALLOCATION</b>","<code>RISK-BOUNDED ROUTING</code>","",f"Equity     <b>{equity:.2f} USDT</b>",f"Free       <b>{free:.2f} USDT</b>",f"Exposure   <b>{exposure:.2f} USDT</b>",""]
 for k,v in allocations.items():out.append(f"{k:<18} <b>{v:.2f} USDT</b>")
 out+=["","<i>Automatic withdrawals are disabled.</i>"];return "\n".join(out)
