def render(name,health,modes,certified=False,failed=()):
 return (f"<b>{name.upper()}</b>\n<code>VENUE CONTROL</code>\n\n"
 f"Market data   {'🟢' if health.get('success_pct',0)>=95 else '🟠'} {health.get('success_pct',0):.0f}%\n"
 f"Latency       <code>{health.get('latency_ms','—')} ms</code>\n\n"
 f"SCAN   {'🟢 ON' if modes.get('scan',True) else '⚫ OFF'}\nPAPER  {'🟢 ON' if modes.get('paper',True) else '⚫ OFF'}\nREAL   {'🟢 ON' if modes.get('real',False) else '🔒 LOCKED'}\n\n"
 f"Certification {'🟢 PASSED' if certified else '🔒 REQUIRED'}"+(f"\nMissing: <code>{', '.join(failed)}</code>" if failed else ""))
