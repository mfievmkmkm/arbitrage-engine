def calculate(amount_out,slippage_pct):
 if amount_out<=0 or slippage_pct<0:return None
 return amount_out*(1-slippage_pct/100)
