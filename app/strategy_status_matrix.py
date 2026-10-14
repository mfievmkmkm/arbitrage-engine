DEFAULT={"futures_futures":{"scan":True,"paper":True,"real":False},"spot_futures":{"scan":True,"paper":True,"real":False},"spot_spot":{"scan":True,"paper":True,"real":False},"funding_arb":{"scan":True,"paper":True,"real":False},"cex_dex":{"scan":True,"paper":False,"real":False}}
def matrix(overrides=None):
 x={k:dict(v) for k,v in DEFAULT.items()}
 for k,v in (overrides or {}).items():x.setdefault(k,{}).update(v)
 return x
