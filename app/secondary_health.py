def snapshot(bundle):
 return {"clients":len(bundle.clients),"spot_future_paper":len(bundle.sf_paper.positions),"runtime":bundle.runtime.runtime.snapshot()}
