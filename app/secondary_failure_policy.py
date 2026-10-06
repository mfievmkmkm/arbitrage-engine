def action(name,error_count,max_errors=3):
 if error_count>=max_errors:return {"strategy":name,"action":"DISABLE_STRATEGY","live_effect":"NONE"}
 return {"strategy":name,"action":"RETRY_LATER","live_effect":"NONE"}
