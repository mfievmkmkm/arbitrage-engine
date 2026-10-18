"""Read-only, pool-scoped token simulation evidence for PAPER, never LIVE.

https://docs.honeypot.is/ishoneypot — percentages, not fractions or basis points.
A received response is an observation, not proof of execution for our wallet,
size or block. Unsupported chains/pools and missing fields stay unknown.
"""
import asyncio
import copy
import time
from decimal import Decimal, InvalidOperation
import aiohttp
from .dex_request_budget import Budget
from .zerox_research import ADDRESS

CHAINS = {1,56,8453}
EXCHANGES = {'uniswap','uniswapv3','pancake','pancake-v3'}


def parse(raw, chain, token, pool, other, observed_at):
    if not isinstance(raw,dict):
        raise ValueError('DEX_TAX_RESPONSE_INVALID')
    pair = raw.get('pair') or {}
    identity = pair.get('pair') or {}
    if (str((raw.get('chain') or {}).get('id'))!=str(chain)
            or str(pair.get('chainId'))!=str(chain)
            or str((raw.get('token') or {}).get('address','')).lower()!=token.lower()
            or str(raw.get('pairAddress','')).lower()!=pool.lower()
            or str(identity.get('address','')).lower()!=pool.lower()
            or {str(identity.get(k,'')).lower() for k in ('token0','token1')}!={token.lower(),other.lower()}):
        raise ValueError('DEX_TAX_SCOPE_MISMATCH')
    if raw.get('simulationSuccess') is not True:
        raise ValueError('DEX_TAX_SIMULATION_FAILED')
    if (raw.get('honeypotResult') or {}).get('isHoneypot') is not False:
        raise ValueError('DEX_TAX_HONEYPOT_OR_UNKNOWN')
    summary = raw.get('summary') or {}
    risk = summary.get('riskLevel')
    if type(risk) not in (int,float) or not 0<=risk<20:
        raise ValueError('DEX_TAX_RISK_OR_UNKNOWN')
    # Unknown flags must not be reinterpreted as a safe result.
    if summary.get('flags') not in ([],None) or raw.get('flags') not in ([],None):
        raise ValueError('DEX_TAX_FLAGS_UNSUPPORTED')
    simulation = raw.get('simulationResult') or {}
    if any(simulation.get(k) is not None for k in ('maxBuy','maxSell')):
        raise ValueError('DEX_TAX_SIZE_LIMIT_UNSUPPORTED')
    fields = {}
    for field in ('buyTax','sellTax','transferTax'):
        value = simulation.get(field)
        if value is None or isinstance(value,bool) or str(value).strip()=='':
            raise ValueError('DEX_TAX_UNKNOWN')
        try:
            tax = Decimal(str(value))
            if not tax.is_finite() or not 0<=tax<=100:
                raise ValueError('DEX_TAX_INVALID')
        except InvalidOperation:
            raise ValueError('DEX_TAX_INVALID') from None
        fields[field+'Bps'] = str(tax*100)
    return dict(fields,source='Honeypot.is',chain_id=chain,token=token.lower(),
                pool=pool.lower(),observed_at=observed_at,
                evidence_mode='POOL_SIMULATION_PAPER_ONLY',execution_verified=False)


class Reader:
    def __init__(self, session=None, clock=time.time):
        self.session,self.clock = session,clock
        self.owned = session is None
        self.cache,self.locks = {},{}
        self.budget = Budget(spacing=1.1,concurrency=2)
        self.blocked_until=0
        self.block_reason=None
        self.requests_sent=0

    async def close(self):
        if self.owned and self.session:
            await self.session.close()

    async def token(self, chain, token, pool, other):
        if type(chain) is not int or chain not in CHAINS:
            raise ValueError('DEX_TAX_CHAIN_UNSUPPORTED')
        if not all(isinstance(x,str) and ADDRESS.fullmatch(x) for x in (token,pool,other)) or token.lower()==other.lower():
            raise ValueError('DEX_TAX_SCOPE_INVALID')
        key=(chain,token.lower(),pool.lower(),other.lower())
        # Bounded cache and single flight per exact simulation scope.
        if len(self.locks)>=512 and key not in self.locks:
            for old in list(self.locks):
                if not self.locks[old].locked():
                    self.locks.pop(old,None); self.cache.pop(old,None)
                    if len(self.locks)<384:break
            if len(self.locks)>=512:
                raise ValueError('DEX_TAX_QUEUE_LIMIT')
        async with self.locks.setdefault(key,asyncio.Lock()):
            cached=self.cache.get(key)
            if cached and 0<=self.clock()-cached[0]<cached[1]:
                if isinstance(cached[2],str):raise ValueError(cached[2])
                return copy.deepcopy(cached[2])
            if self.clock()<self.blocked_until:
                raise ValueError(self.block_reason)
            permit=await self.budget.acquire()
            try:
                result=await self._token(chain,token,pool,other)
            except ValueError as exc:
                self.cache[key]=(self.clock(),15,str(exc))
                raise
            else:
                self.cache[key]=(result['observed_at'],60,result)
                return copy.deepcopy(result)
            finally:
                self.budget.release(permit)

    async def _token(self, chain, token, pool, other):
        if self.clock()<self.blocked_until:
            raise ValueError(self.block_reason)
        if self.session is None:
            self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6))
        start=self.clock()
        try:
            self.requests_sent+=1
            async with self.session.get('https://api.honeypot.is/v2/IsHoneypot',
                                       params=dict(address=token,chainID=str(chain),pair=pool)) as response:
                if response.status!=200:
                    reason='DEX_TAX_HTTP_'+str(response.status)
                    if response.status in (401,403,429) or response.status>=500:
                        delay=60 if response.status in (401,403,429) else 5
                        if response.status==429:
                            try:
                                retry=float(getattr(response,'headers',{}).get('Retry-After',60))
                                if retry==retry and 1<=retry<=3600:delay=max(delay,retry)
                            except (ValueError,TypeError):pass
                        self.blocked_until=self.clock()+delay
                        self.block_reason=reason
                    raise ValueError(reason)
                raw=await response.json()
            if not 0<=self.clock()-start<=6:
                raise ValueError('DEX_TAX_RESPONSE_STALE')
            return parse(raw,chain,token,pool,other,start)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError,asyncio.TimeoutError,TypeError,AttributeError,ValueError) as exc:
            reason=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('DEX_TAX_') else 'DEX_TAX_UNAVAILABLE'
            raise ValueError(reason) from None

    async def route(self, quote):
        chain=quote['chain_id']
        sell,buy=quote['sell_token'].lower(),quote['buy_token'].lower()
        paths=quote.get('route')
        if not isinstance(paths,list) or not 1<=len(paths)<=2:
            raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
        pools=[]
        # Initial scope intentionally excludes multi-hop tokens, hooks and RFQs.
        for path in paths:
            if not isinstance(path,list) or len(path)!=1 or not isinstance(path[0],dict):
                raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
            hop=path[0];pool=hop.get('pool')
            if (hop.get('exchange') not in EXCHANGES or not isinstance(pool,str) or not ADDRESS.fullmatch(pool)
                    or str(hop.get('tokenIn','')).lower()!=sell or str(hop.get('tokenOut','')).lower()!=buy):
                raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
            if pool.lower() not in pools:pools.append(pool.lower())
        rows={side:[] for side in ('sellToken','buyToken')}
        for pool in pools:
            for side,token,other in [('sellToken',sell,buy),('buyToken',buy,sell)]:
                row=await self.token(chain,token,pool,other)
                if not 0<=self.clock()-row['observed_at']<60:
                    raise ValueError('DEX_TAX_EVIDENCE_STALE')
                rows[side].append(row)
        metadata={}
        for side,evidence in rows.items():
            metadata[side]={field:str(max(Decimal(row[field]) for row in evidence))
                            for field in ('buyTaxBps','sellTaxBps','transferTaxBps')}
            metadata[side]['evidence']=evidence
        return metadata
