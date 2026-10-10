CHECKS=("ci_green","restart_reconciled","private_verified","fees_verified","funding_known","position_mode_verified","clock_skew_ok","min_order_ok","book_fresh","no_unknown_orders","operator_armed","withdrawals_disabled_attested")
def evaluate(evidence):
 missing=tuple(x for x in CHECKS if not evidence.get(x,False));return {"allowed":not missing,"missing":missing}
