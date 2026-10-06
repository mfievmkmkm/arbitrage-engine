def render(risk,supervisor,stop):
 s=risk.state;return ("<b>RISK CENTER</b>\n<code>FAIL-CLOSED CONTROLS</code>\n\n"
 f"Global state     {'🔴 HALTED' if s.halted else '🟢 NORMAL'}\nOperator STOP   {'🔴 ACTIVE' if stop.stopped else '🟢 CLEAR'}\nPrivate state   {'🟢 VERIFIED' if supervisor.private_verified else '🟠 UNVERIFIED'}\nRestart state   {'🟢 CLEAN' if supervisor.restart_clean else '🔴 RECONCILE'}\nUnknown orders  <b>{len(supervisor.unknown_orders)}</b>\n\n"
 f"Daily PnL       <b>{s.paper_daily_pnl:+.4f} USDT</b>\nEngine errors   <b>{s.engine_errors}</b>\n\n<i>Any untrusted exposure state blocks new LIVE entries.</i>")
