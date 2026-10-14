def render(t,net_usd=0,spread=0,exposure=0,age_s=0):
 return (f"<b>LIVE POSITION</b>\n<code>{t.symbol}</code>\n\n"
 f"LONG   <b>{t.long_venue.upper()}</b>\nSHORT  <b>{t.short_venue.upper()}</b>\n"
 f"Exposure  <b>{exposure:.4f} USDT</b>\n\n"
 f"NET PnL   <b>{net_usd:+.4f} USDT</b>\nSpread    <b>{spread:+.3f}%</b>\nAge       <code>{age_s:.0f}s</code>\n\n"
 "Close state: <b>PRIVATE VERIFIED required</b>")
