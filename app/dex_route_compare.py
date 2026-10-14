def best(quotes):
 valid=[q for q in quotes if q.valid]
 if not valid:return None
 return max(valid,key=lambda q:q.amount_out-q.gas_usd)
