"""Strategy B adapters share ONE existing virtual account and position lock."""
from .dex_transfer_paper import Service
from .dex_transfer_source import Source,dex


class Router:
    enabled_name='market_signals'
    interval=3
    def __init__(self,feed,paired):
        self.feed,self.paper=feed,feed.paper
        self.handlers={v:Service(feed,paired,v) for v in ('bybit','mexc')}
        self.entry_enabled=True;self.paused=lambda:False
        self.index=0;self.last_handler=self.handlers['bybit']
        for handler in self.handlers.values():handler.paused=lambda:self.paused()
        feed.dex_transfer=self;feed.paper.dex_transfer=self

    async def init(self):
        for venue in self.handlers:
            if venue not in self.feed.trade_venues:
                trace=await self.feed.journal.start('B','*',venue,'DEX','observe','configuration')
                await self.feed.journal.finish(trace,'REJECTED',venue.upper()+': нужен spot API чтения сетей и комиссии')

    def handler(self,buy,sell):
        venue=next((v for v in (buy,sell) if not dex(v)),None)
        if venue not in self.handlers:raise ValueError('DEX_TRANSFER_CEX_UNSUPPORTED')
        handler=self.handlers[venue];handler.paper=self.feed.paper
        handler.entry_enabled=self.entry_enabled
        return handler

    @property
    def checked(self):return sum(h.checked for h in self.handlers.values())
    @property
    def last_reason(self):return self.last_handler.last_reason
    @property
    def last_economics(self):return getattr(self.last_handler,'last_economics',None)

    def catalog(self):return [r for h in self.handlers.values() for r in h.catalog()]
    def prioritize_signal(self,signal,post):return sum(h.prioritize_signal(signal,post) for h in self.handlers.values())

    async def review(self,symbol,buy,sell,read_accounts=False,**kwargs):
        return await self.handler(buy,sell).review(symbol,buy,sell,read_accounts,**kwargs)

    async def open(self,review):
        e=review['signal'];return await self.handler(e['buy'],e['sell']).open(review)

    async def monitor_unlocked(self):
        p=self.feed.paper.state['position'];s=p['signal']
        return await self.handler(s['buy'],s['sell']).monitor_unlocked()

    async def cancel_unlocked(self,state,p):
        s=p['signal'];return await self.handler(s['buy'],s['sell']).cancel_unlocked(state,p)

    def format(self,e,include_code=True):return Source.format(self,e,include_code)

    async def cycle(self):
        # Only connected spot account adapters enter the continuous transfer scan.
        venues=[v for v in self.handlers if v in self.feed.trade_venues]
        if not venues:
            await self.feed.paper.monitor(self.feed);return []
        venue=venues[self.index%len(venues)];self.index+=1
        self.last_handler=self.handler(venue,'dex56')
        return await self.last_handler.cycle()
