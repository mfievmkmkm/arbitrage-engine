def home(system,risk,strategies,incidents):
 sev="🔴" if incidents else ("🟠" if risk.get("halted") else "🟢");out=["<b>ARBITRAGE // CONTROL</b>","<code>EXECUTION • RISK • RESEARCH</code>","",f"{sev} <b>{'ATTENTION REQUIRED' if incidents else 'SYSTEM NOMINAL'}</b>",f"Scanner <b>{system.get('scanner','—')}</b> · Venues <b>{system.get('venues','—')}</b>",f"LIVE <b>{system.get('live','LOCKED')}</b> · Private <b>{system.get('private','UNVERIFIED')}</b>","", "<b>STRATEGIES</b>"]
 for k,v in strategies.items():out.append(f"{'🟢' if v.get('scan') else '⚫'} {k:<18} PAPER {'ON' if v.get('paper') else 'OFF'} · REAL {'ON' if v.get('real') else '🔒'}")
 if incidents:out+=["","<b>INCIDENTS</b>"]+[f"🔴 {x.code} · <code>{x.trade_id}</code>" for x in incidents[:3]]
 return "\n".join(out)
