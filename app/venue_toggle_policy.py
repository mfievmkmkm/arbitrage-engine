def set_mode(current,mode,value,certified=False,acceptance=False):
 x=dict(current);mode=mode.lower()
 if mode=="real" and value and not (certified and acceptance):return x,False,"REAL_LOCKED"
 if mode not in ("scan","paper","real"):return x,False,"UNKNOWN_MODE"
 x[mode]=bool(value);return x,True,"OK"
