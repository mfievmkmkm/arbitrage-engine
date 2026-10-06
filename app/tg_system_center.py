def render(scanner,runtime):
 h=scanner.health.snapshot();return ("<b>SYSTEM</b>\n<code>RUNTIME & DATA PLANE</code>\n\n"
 f"Scanner      {'🟠 PAUSED' if scanner.paused else '🟢 RUNNING'}\nVenues       <b>{len(scanner.clients)}/{len(scanner.ids)}</b>\nLast scan    <code>{scanner.last_scan or '—'}</code>\n\n"
 f"Strategy counts  <code>{runtime.counts()}</code>\nHealth rows      <b>{len(h)}</b>")
