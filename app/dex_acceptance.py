REQUIRED=("quote_simulation","gas","price_impact","slippage","token_contract","token_tax","network_state","wallet_boundary","min_received","failure_matrix")
def evaluate(results):
 failed=tuple(x for x in REQUIRED if not results.get(x,False));return not failed,failed
