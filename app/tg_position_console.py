def paper(paper,sf_paper=None):
 rows=list(paper.positions.values());sf=list(sf_paper.positions.values()) if sf_paper else [];out=["<b>POSITIONS</b>","<code>PAPER / RESEARCH</code>","",f"Futures↔Futures  <b>{len(rows)}</b>",f"Spot↔Futures     <b>{len(sf)}</b>"]
 for p in rows[:5]:out+=["",f"<b>{p.symbol}</b>  {p.buy.upper()} ↔ {p.sell.upper()}",f"NET now  <b>{p.current_net_usd:+.4f} USDT</b>"]
 for p in sf[:5]:out+=["",f"<b>{p.base}</b>  {p.exchange.upper()} · {p.direction}",f"NET now  <b>{p.net:+.4f} USDT</b>"]
 return "\n".join(out)
