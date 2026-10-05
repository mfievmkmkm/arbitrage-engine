def recovery_action(long_filled,short_filled,remaining_edge,complete_cost,flatten_cost,tolerance=1e-8):
    mismatch=abs(long_filled-short_filled)
    if mismatch<=tolerance:return "HEDGED"
    if remaining_edge<=0:return "FLATTEN"
    return "COMPLETE" if complete_cost<flatten_cost else "FLATTEN"
def restart_action(private_ready,unexpected_exposure):
    if not private_ready:return "NO_NEW_TRADES"
    if unexpected_exposure:return "RECONCILE_AND_FLATTEN"
    return "RESUME"
