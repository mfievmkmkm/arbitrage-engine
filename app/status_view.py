def render(h,venues,scanner_age=None):
 icon={'HALTED':'🔴','OBSERVATION_ONLY':'🟡','SAFE':'🟢','LIVE_READY':'🟢'}.get(h.status,'⚪')
 age='—' if scanner_age is None else f'{scanner_age:.1f}s'
 return f"{icon} Система: {h.status}\n📡 Market data: {'OK' if h.market_ok else 'FAIL'} • age {age}\n🔐 Private API: {'OK' if h.private_ok else 'OFF/FAIL'}\n🗄 DB: {'OK' if h.db_ok else 'FAIL'}\n🛡 Risk: {'OK' if h.risk_ok else 'HALT'}\n🏦 Бирж подключено: {venues}\n⚙️ LIVE ready: {'ДА' if h.live_ready else 'НЕТ'}"
