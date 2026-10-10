from decimal import Decimal,ROUND_DOWN
def floor_step(value,step):
    if not step:return value
    v=Decimal(str(value));s=Decimal(str(step))
    return float((v/s).to_integral_value(rounding=ROUND_DOWN)*s)
def quantity_for_notional(notional,price,step=None,minimum=None):
    if price<=0:return None
    qty=floor_step(notional/price,step)
    if qty<=0 or (minimum is not None and qty<minimum):return None
    return qty
