class Coordinator:
 def __init__(self,spot_future_engine):self.sf=spot_future_engine
 def spot_future(self,rows,entry_edge):
  closed=[];opened=[]
  for x in rows:
   closed.extend(self.sf.update(x))
   if x["hypothetical_edge"]>=entry_edge:
    p=self.sf.open(x)
    if p:opened.append(p)
  return opened,closed
