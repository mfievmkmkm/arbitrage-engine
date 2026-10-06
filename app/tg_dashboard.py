from .tg_format import age

def home(scanner,paper,risk,runtime,live_stop):
 rs=risk.state;counts=runtime.counts();health=scanner.health.snapshot();online=sum(1 for x in scanner.ids if x in scanner.clients)
 return ("<b>ARBITRAGE ENGINE</b>\n<code>MARKET NEUTRAL • EXECUTION FIRST</code>\n\n"
 f"<b>Система</b>\n{'🟢' if not rs.halted else '🔴'} Risk  •  {'🟢' if not scanner.paused else '🟠'} Scanner  •  {'🔴 STOP' if live_stop.stopped else '🔒 LIVE LOCKED'}\n"
 f"Биржи <b>{online}/{len(scanner.ids)}</b>  ·  цикл <code>{age(scanner.last_scan)}</code>\n\n"
 f"<b>Поток</b>\nFutures↔Futures  <b>{counts.get('futures_futures',0)}</b>\nSpot↔Futures  <b>{counts.get('spot_futures',0)}</b>\nSpot↔Spot  <b>{counts.get('spot_spot',0)}</b>\n\n"
 f"<b>Paper</b>  {len(paper.positions)}/{paper.max_positions} позиций\nDay PnL  <b>{rs.paper_daily_pnl:+.4f} USDT</b>\n\n<i>LIVE не активируется без reconciliation и acceptance.</i>")

def opportunities(rows):
 if not rows:return "<b>РЫНОК</b>\n\nПодходящих исполнимых возможностей сейчас нет."
 out=["<b>РЫНОК • FUTURES↔FUTURES</b>","<code>EXECUTABLE NET • TOP 8</code>",""]
 for i,x in enumerate(rows[:8],1):out.append(f"<b>{i:02d}  {x['symbol']}</b>\n<code>{x['buy']} LONG  ↔  {x['sell']} SHORT</code>\nNET <b>{x['hypothetical_edge']:+.3f}%</b>  ·  exec {x['executable']:.3f}%  ·  funding {x.get('funding_pct',0):+.3f}%")
 return "\n\n".join(out)

def venues(scanner):
 h=scanner.health.snapshot();out=["<b>ПЛОЩАДКИ</b>","<code>PUBLIC MARKET DATA</code>",""]
 for name in scanner.ids:
  x=h.get(name,{});ok=name in scanner.clients;out.append(f"{'🟢' if ok else '🔴'} <b>{name.upper()}</b>  ·  {x.get('success_pct',0):.0f}%  ·  <code>{x.get('latency_ms','—')} ms</code>")
 out+=["","<i>REAL для каждой площадки включается только после отдельной сертификации.</i>"];return "\n".join(out)
