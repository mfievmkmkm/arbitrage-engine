def state(acceptance_ok,wallet_safe,paper_samples):
 if not acceptance_ok:return "RESEARCH_LOCKED"
 if not wallet_safe:return "WALLET_LOCKED"
 if paper_samples<100:return "PAPER"
 return "SEMI_AUTO_REVIEW"
