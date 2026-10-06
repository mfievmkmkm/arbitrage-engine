def render(startup,private_snapshot,trades):
 lines=['🚦 STARTUP CHECK',f'Режим: {startup.mode}',f'Результат: {startup.reason}',f'Runtime-сделок: {len(trades)}']
 if not private_snapshot:lines.append('🔐 Private API: не подключён')
 for venue,data in private_snapshot.items():
  h=data['health'];b=data.get('balance');bal='—' if b is None else f'${b.free:.2f} free / ${b.total:.2f} total';icon='🟢' if h.ok else '🔴'
  lines.append(f"{icon} {venue}: positions {len(data['positions'])}, orders {len(data['orders'])}, {bal}")
 if not startup.safe:lines.append('⛔ LIVE заблокирован до reconciliation.')
 return '\n'.join(lines)
