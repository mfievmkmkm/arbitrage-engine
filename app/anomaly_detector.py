def detect(latency_ms,baseline_latency,slippage,baseline_slippage,incident_rate):
 out=[]
 if baseline_latency>0 and latency_ms>baseline_latency*2:out.append("LATENCY_SPIKE")
 if baseline_slippage>=0 and slippage>max(.1,baseline_slippage*2):out.append("SLIPPAGE_SPIKE")
 if incident_rate>.05:out.append("INCIDENT_RATE_HIGH")
 return tuple(out)
