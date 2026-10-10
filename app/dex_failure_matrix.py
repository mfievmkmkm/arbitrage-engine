CASES=("quote_timeout","quote_stale","route_changed","gas_spike","price_impact","token_tax_unknown","contract_mismatch","network_down","deposit_disabled","wallet_low_gas","min_received_missing","swap_reverted","cex_leg_partial","wallet_state_unknown")
def complete(results):
 failed=tuple(x for x in CASES if not results.get(x,False));return not failed,failed
