def verified(snapshot,symbol,venues,tolerance=1e-10):
 if snapshot is None:return False
 for v in venues:
  x=snapshot.get((v,symbol),snapshot.get(v))
  if x is None:return False
  if isinstance(x,dict):x=x.get("base",x.get("qty",x.get("contracts")))
  if x is None or abs(float(x))>tolerance:return False
 return True
