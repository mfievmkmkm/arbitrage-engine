CRITICAL=("BORROW","PRIVATE","UNKNOWN_ORDER","POSITION_MODE","BALANCE_MISMATCH")
def classify(reason):
 u=str(reason).upper();return "HALT" if any(x in u for x in CRITICAL) else "BLOCK_ENTRY"
