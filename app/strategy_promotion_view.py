def render(name,evidence):
 return f"<b>{name}</b>\n<code>PROMOTION GATE</code>\n\nStatus  {'🟢 ELIGIBLE' if evidence.allowed else '🔒 LOCKED'}\nReasons <code>{', '.join(evidence.reasons) if evidence.reasons else '—'}</code>\n\nPromotion never changes LIVE authority automatically."
