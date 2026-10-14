def to_base_levels(levels,contract_size):
 size=float(contract_size or 1)
 return [[float(price),float(amount)*size] for price,amount,*_ in levels]
def common_base_qty(notional,price):
 return 0 if price<=0 else notional/price
