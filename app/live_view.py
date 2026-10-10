def render_trade(symbol,long_venue,short_venue,long_entry,short_entry,current_long,current_short,net,best_net,age_seconds,state):
 spread=(current_short-current_long)/current_long*100 if current_long else 0
 return (f"📈 {symbol}\n🟢 LONG {long_venue}: {long_entry:.6g} → {current_long:.6g}\n🔴 SHORT {short_venue}: {short_entry:.6g} → {current_short:.6g}\n↔️ Spread сейчас: {spread:.3f}%\n💰 NET сейчас: ${net:.4f}\n🏆 Max NET: ${best_net:.4f}\n⏱ {int(age_seconds)} сек. • {state}")
