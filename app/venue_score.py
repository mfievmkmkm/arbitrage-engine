def score(success_pct,latency_ms,opportunities,net_usd,incidents):
 return max(0,float(success_pct))*0.25 + min(max(opportunities,0),100)*0.15 + max(min(net_usd,10),-10)*3 - min(max(latency_ms,0),5000)/200 - max(incidents,0)*5
