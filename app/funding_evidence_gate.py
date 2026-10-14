def evaluate(rate,interval,next_ts):
 if rate is None:return False,"FUNDING_RATE_UNKNOWN"
 if not interval:return False,"FUNDING_INTERVAL_UNKNOWN"
 if next_ts is None:return False,"NEXT_FUNDING_UNKNOWN"
 return True,"OK"
