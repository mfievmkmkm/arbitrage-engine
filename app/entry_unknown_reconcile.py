from .unknown_submit_policy import decide
async def reconcile(executor,client_id,private_snapshot,exposure):
 try:o=await executor.order_by_client_id(client_id)
 except Exception:o=None
 lookup="FOUND" if o is not None else "MISSING";trusted=private_snapshot is not None;d=decide(lookup,trusted,exposure);return d,o
