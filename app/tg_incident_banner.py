def render(supervisor,stop):
 items=[]
 if stop.stopped:items.append("🛑 <b>OPERATOR STOP ACTIVE</b>")
 if supervisor.unknown_orders:items.append(f"🔴 <b>UNKNOWN ORDERS: {len(supervisor.unknown_orders)}</b>")
 if not supervisor.restart_clean:items.append("🔴 <b>RESTART RECONCILIATION REQUIRED</b>")
 if not supervisor.private_verified:items.append("🟠 Private positions not verified")
 return "\n".join(items)
