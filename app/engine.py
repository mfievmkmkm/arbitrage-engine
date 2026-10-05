"""Public-data discovery. No order placement."""
import asyncio, time
from dataclasses import dataclass
import ccxt.async_support as ccxt

FEE_BPS={"binance":5.0,"bybit":5.5,"okx":5.0,"bitget":6.0,"gateio":7.5,"mexc":6.0,"bingx":6.0}
ALLOWED=set(FEE_BPS)

@dataclass
class Quote:
    exchange:str; symbol:str; bids:list; asks:list; fetched:float

def vwap(levels,qty):
    rem,total=qty,0.0
    for price,amount in levels:
        take=min(rem,amount); total+=take*price; rem-=take
        if rem<=1e-10: return total/qty
    return None

def evaluate(buy,sell,notional,max_age,now=None):
    now=time.time() if now is None else now
    if buy.symbol!=sell.symbol or buy.exchange==sell.exchange:return None
    if any(now-q.fetched>max_age or q.fetched>now+2 for q in (buy,sell)):return None
    if not buy.asks or not buy.bids or not sell.bids or not sell.asks:return None
    qty=notional/buy.asks[0][0]
    entry_buy=vwap(buy.asks,qty); entry_sell=vwap(sell.bids,qty)
    # What we could execute to close the same hedge now.
    exit_buy=vwap(buy.bids,qty); exit_sell=vwap(sell.asks,qty)
    if None in (entry_buy,entry_sell,exit_buy,exit_sell):return None
    raw=(sell.bids[0][0]-buy.asks[0][0])/buy.asks[0][0]*100
    executable=(entry_sell-entry_buy)/entry_buy*100
    exit_spread=(exit_sell-exit_buy)/exit_buy*100
    fee=2*(FEE_BPS[buy.exchange]+FEE_BPS[sell.exchange])/100
    return dict(symbol=buy.symbol,buy=buy.exchange,sell=sell.exchange,
        raw=raw,executable=executable,fee_pct=fee,
        hypothetical_edge=executable-fee,notional=notional,ts=now,
        entry_buy=entry_buy,entry_sell=entry_sell,
        exit_buy=exit_buy,exit_sell=exit_sell,exit_spread=exit_spread,
        age_buy=now-buy.fetched,age_sell=now-sell.fetched)

class Scanner:
    def __init__(self,exchanges,notional,max_age):
        self.ids=[x for x in exchanges if x in ALLOWED]; self.notional=notional; self.max_age=max_age
        self.clients={};self.symbols={};self.errors={};self.paused=False;self.last_scan=None
    async def start(self):
        async def init(name):
            c=getattr(ccxt,name)({"enableRateLimit":True,"options":{"defaultType":"swap"}})
            try:
                markets=await c.load_markets()
                syms={m["symbol"] for m in markets.values() if m.get("swap") and m.get("linear") and m.get("settle")=="USDT" and m.get("active") is not False}
                self.clients[name]=c;self.symbols[name]=syms
            except Exception as exc:self.errors[name]=type(exc).__name__;await c.close()
        await asyncio.gather(*(init(x) for x in self.ids))
    async def close(self):
        await asyncio.gather(*(c.close() for c in self.clients.values()),return_exceptions=True)
    async def scan(self):
        if self.paused:return []
        counts={}
        for syms in self.symbols.values():
            for s in syms:counts[s]=counts.get(s,0)+1
        common=sorted(s for s,n in counts.items() if n>=2)[:30]
        sem=asyncio.Semaphore(5)
        async def fetch(name,symbol):
            async with sem:
                try:
                    b=await asyncio.wait_for(self.clients[name].fetch_order_book(symbol,limit=20),timeout=8)
                    return Quote(name,symbol,b["bids"],b["asks"],time.time())
                except Exception as exc:self.errors[name]=type(exc).__name__;return None
        rs=await asyncio.gather(*(fetch(n,s) for s in common for n in self.clients if s in self.symbols[n]))
        grouped={}
        for q in rs:
            if q:grouped.setdefault(q.symbol,[]).append(q)
        now=time.time();ops=[]
        for qs in grouped.values():
            for a in qs:
                for b in qs:
                    r=evaluate(a,b,self.notional,self.max_age,now)
                    if r and r["hypothetical_edge"]>0:ops.append(r)
        self.last_scan=now
        return sorted(ops,key=lambda x:x["hypothetical_edge"],reverse=True)[:30]
