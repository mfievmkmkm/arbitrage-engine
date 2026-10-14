def render(events):
 out=["<b>TRADE TIMELINE</b>","<code>DURABLE EXECUTION AUDIT</code>",""]
 for x in events[-12:]:out.append(f"<code>{x.get('ts','—')}</code>  <b>{x.get('kind',x.get('phase','EVENT'))}</b>\n{x.get('venue','')} {x.get('side','')} {x.get('qty','')}")
 return "\n".join(out)
