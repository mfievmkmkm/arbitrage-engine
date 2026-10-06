def render(supervisor,trades,realized_net,stop):
 r=supervisor.readiness()
 rows=["⚡ MICRO-LIVE CONTROL",f"Статус: {'🟢 READY' if r.ready and not stop.stopped else '🔒 LOCKED'}",f"Открыто: {len(trades)}",f"Realized NET: {realized_net:+.4f} USD"]
 if stop.stopped:rows.append("STOP: "+stop.reason)
 if r.reasons:rows.append("Блокировки: "+", ".join(r.reasons))
 rows.append("Новые входы: "+("РАЗРЕШЕНЫ" if r.ready and not stop.stopped else "ЗАПРЕЩЕНЫ"))
 return "\n".join(rows)
