def render(trade,phase,net,spread,fees,funding,slippage,private_ok):return f"""<b>LIVE // {trade.symbol}</b>
<code>{phase}</code>

LONG   <b>{trade.long_venue.upper()}</b>
SHORT  <b>{trade.short_venue.upper()}</b>
BASE   <b>{trade.base_qty:.8f}</b>

NET PnL     <b>{net:+.4f} USDT</b>
Spread      <b>{spread:+.3f}%</b>
Fees        <b>{fees:.4f}</b>
Funding     <b>{funding:+.4f}</b>
Slippage    <b>{slippage:.4f}</b>

Private state  <b>{'VERIFIED' if private_ok else 'UNTRUSTED'}</b>
Close complete only after private exposure = 0."""
