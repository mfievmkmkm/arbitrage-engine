def render(incidents):
 out=["<b>INCIDENT CENTER</b>","<code>EXECUTION SAFETY</code>",""]
 if not incidents:return "\n".join(out+["🟢 No active incidents"])
 for x in incidents:out.append(f"{'🔴' if x.severity=='CRITICAL' else '🟠'} <b>{x.code}</b>\nTrade <code>{x.trade_id}</code>\nAction: <code>{x.action}</code>")
 return "\n\n".join(out)
