import asyncio
class Loop:
 def __init__(self,service,bot,chat_id,interval=3):self.service=service;self.bot=bot;self.chat_id=chat_id;self.interval=interval;self.messages={};self.task=None
 async def _run(self):
  while True:
   active=set()
   for trade,text in self.service.cards():
    active.add(trade.trade_id);mid=self.messages.get(trade.trade_id)
    try:
     if mid:await self.bot.edit_message_text(text,self.chat_id,mid,parse_mode="HTML")
     else:self.messages[trade.trade_id]=(await self.bot.send_message(self.chat_id,text,parse_mode="HTML")).message_id
    except Exception:pass
   for k in list(self.messages):
    if k not in active:self.messages.pop(k,None)
   await asyncio.sleep(self.interval)
 def start(self):
  if not self.task:self.task=asyncio.create_task(self._run())
 async def stop(self):
  if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True);self.task=None
