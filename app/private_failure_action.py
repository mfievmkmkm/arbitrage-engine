def action(exposure_possible,private_snapshot_available):
 if not exposure_possible:return "FAIL_CLOSED"
 if not private_snapshot_available:return "GLOBAL_HALT_EXPOSURE_UNKNOWN"
 return "PROTECTIVE_FLATTEN_THEN_VERIFY"
