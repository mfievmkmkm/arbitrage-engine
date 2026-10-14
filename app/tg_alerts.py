def unknown_order(order_id,venue,symbol):return f"🔴 <b>CRITICAL • UNKNOWN ORDER</b>\n\nVenue: <b>{venue.upper()}</b>\nSymbol: <code>{symbol}</code>\nOrder: <code>{order_id}</code>\n\nNew entries are blocked. Reconciliation is required."
def daily_stop(pnl,limit):return f"🛑 <b>DAILY STOP</b>\nPnL: <b>{pnl:+.4f} USDT</b>\nLimit: <b>{limit:.4f} USDT</b>\n\nNew entries are disabled."
