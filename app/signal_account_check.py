"""Only account balance and trading-fee reads; no signing or order API."""
import asyncio
from .strategy_math import D, plain


class Accounts:
    def __init__(self,clients,clock):
        self.clients,self.clock=clients,clock

    def client(self,venue):
        return self.clients.get(venue) or (self.clients.get('gateio') if venue=='gate' else None)

    async def read(self,venue,symbol):
        c=self.client(venue)
        if c is None:
            raise ValueError(venue+': не подключён ключ с правами чтения spot')
        if c.has.get('fetchBalance') is not True or c.has.get('fetchTradingFee') is not True:
            raise ValueError(venue+': нет полного API чтения баланса/комиссии')
        started=self.clock()
        balance,fee=await asyncio.gather(
            asyncio.wait_for(c.fetch_balance({'type':'spot'}),10),
            asyncio.wait_for(c.fetch_trading_fee(symbol),10))
        if fee.get('symbol')!=symbol or fee.get('taker') is None or not 0<=D(fee['taker'])<1:
            raise ValueError(venue+': некорректные данные комиссии')
        base=symbol.split('/')[0];funds={}
        for asset in ('USDT',base):
            row=balance.get(asset) or {}
            free=row.get('free',(balance.get('free') or {}).get(asset))
            if free is not None and (D(free)<0 or D(row.get('debt') or 0)>0):
                raise ValueError(venue+': отрицательный остаток или долг '+asset)
            funds[asset]=str(D(free)) if free is not None else None
        if self.clock()-started>15:
            raise ValueError('Данные аккаунта устарели')
        return plain(dict(venue=venue,ts=self.clock(),free=funds,taker=str(D(fee['taker'])),
                          scope='SPOT_READ_API',fee_currency_verified=False))

    async def check(self,symbol,buy,sell):
        results=await asyncio.gather(self.read(buy,symbol),self.read(sell,symbol),return_exceptions=True)
        out={};reasons=[]
        for venue,r in zip((buy,sell),results):
            if isinstance(r,Exception):
                reasons.append(str(r) if isinstance(r,ValueError) else venue+': '+type(r).__name__)
            else:out[venue]=r
        return dict(accounts=out,reasons=reasons,ts=self.clock(),execution_authorized=False)
