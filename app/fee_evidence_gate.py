def evaluate(schedule,verified_at,max_age_s,now):
 if schedule is None:return False,"FEE_UNKNOWN"
 if verified_at is None or now-verified_at>max_age_s:return False,"FEE_STALE"
 return True,"OK"
