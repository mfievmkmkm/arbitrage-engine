def classify(durable_phase,private_state,runtime_present):
 if durable_phase=="ENTRY_SUBMITTING" and private_state=="UNKNOWN":return "GLOBAL_HALT"
 if durable_phase=="ENTRY_SUBMITTING" and private_state=="FLAT":return "ABORT_AND_MARK"
 if durable_phase in ("HEDGED_PRIVATE_VERIFIED","OPEN") and not runtime_present:return "RESTORE_FROM_PRIVATE"
 if durable_phase=="EXIT_SUBMITTING" and private_state=="FLAT":return "MARK_CLOSED_VERIFIED"
 if durable_phase=="EXIT_SUBMITTING":return "RESUME_CLOSE_RECONCILIATION"
 return "REVIEW"
