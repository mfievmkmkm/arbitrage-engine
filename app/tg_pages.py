def strategies(runtime):
 c=runtime.counts();return ("<b>СТРАТЕГИИ</b>\n<code>SCAN / PAPER / REAL</code>\n\n"
 f"01  <b>Futures ↔ Futures</b>\n    SCAN 🟢   PAPER 🟢   REAL 🔒\n    opportunities: <b>{c.get('futures_futures',0)}</b>\n\n"
 f"02  <b>Spot ↔ Futures</b>\n    SCAN 🟢   PAPER 🟡   REAL 🔒\n    opportunities: <b>{c.get('spot_futures',0)}</b>\n\n"
 f"03  <b>Spot ↔ Spot</b>\n    SCAN 🟡   PAPER 🟡   REAL 🔒\n    opportunities: <b>{c.get('spot_spot',0)}</b>\n\n"
 "04  <b>CEX ↔ DEX</b>\n    RESEARCH 🟡   PAPER 🔒   REAL 🔒\n\n05  <b>Funding Arbitrage</b>\n    SCAN 🟡   PAPER 🟡   REAL 🔒")

def dex():return "<b>DEX LAB</b>\n<code>RESEARCH ENVIRONMENT</code>\n\nQuote engine      🟡 foundation\nRoute validation  🟢 ready\nToken policy      🟢 ready\nGas / impact      🟢 ready\nWallet execution  🔴 disabled\nLIVE              🔒 locked\n\n<i>Никаких on-chain транзакций до отдельного DEX acceptance.</i>"
