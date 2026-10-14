ORDER={"UNKNOWN_ORDER":100,"POSITION_UNKNOWN":95,"PRIVATE_UNVERIFIED":90,"OPERATOR_STOP":85,"DAILY_STOP":80,"MARKET_DATA":50}
def highest(items):return max(items,key=lambda x:ORDER.get(x,0)) if items else None
