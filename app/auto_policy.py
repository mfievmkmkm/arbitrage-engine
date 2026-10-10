def mode(gate,live_enabled):
 if not live_enabled:return "DISABLED"
 if not gate.allowed:return "SEMI_AUTO"
 return "AUTO_ELIGIBLE"
