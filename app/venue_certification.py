REQUIRED=("client_order_id","reduce_only","private_positions","fees","funding","position_mode","clock","min_notional")
def evaluate(evidence):
 failed=tuple(x for x in REQUIRED if not evidence.get(x,False));return not failed,failed
