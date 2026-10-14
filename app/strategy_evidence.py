def row(x):
 keys=("strategy","symbol","buy","sell","exchange","direction","hypothetical_edge","net","carry_pct","executable","funding_pct","fee_pct","safety_pct","notional","base_qty");return {k:x.get(k) for k in keys if k in x}
def paper_safe(x):return bool(x.get("book_fresh",False) and x.get("fees_verified",False) and x.get("funding_known",False))
