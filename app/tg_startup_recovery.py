def render(x):
 out=["<b>STARTUP // RECOVERY</b>","<code>DATABASE-FIRST RECONCILIATION</code>","",f"State  <b>{'SAFE' if x['safe'] else 'LOCKED'}</b>",f"Reason <code>{x['reason']}</code>",f"Active durable trades <b>{len(x['active'])}</b>",f"Unknown intents <b>{len(x['unknown_intents'])}</b>"]
 for a in x["actions"]:out.append(f"\n• <code>{a['trade_id']}</code> → <b>{a['action']}</b>\n  {a['reason']}")
 return "\n".join(out)
