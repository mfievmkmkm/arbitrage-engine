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
from .evidence_error import EvidenceError

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
    detected = (raw.get('honeypotResult') or {}).get('isHoneypot')
    if detected is True:
        raise EvidenceError('DEX_TAX_HONEYPOT_DETECTED', observed=True, threshold=False,
                            simulation_success=raw.get('simulationSuccess'))
    if raw.get('simulationSuccess') is not True:
        # Classify the documented error without persisting untrusted free text.
        message = str(raw.get('simulationError', '')).lower()
        category = ('INSUFFICIENT_LIQUIDITY' if 'liquidity' in message else
                    'REVERT' if any(x in message for x in ('revert','failed')) else 'UNCLASSIFIED')
        raise EvidenceError('DEX_TAX_SIMULATION_FAILED', simulation_success=raw.get('simulationSuccess'),
                            simulation_error_category=category, observed=False, threshold=True)
    if detected is not False:
        raise EvidenceError('DEX_TAX_HONEYPOT_UNKNOWN', observed=detected, threshold=False)
    summary = raw.get('summary') or {}
    risk = summary.get('riskLevel')
    if type(risk) not in (int,float):
        raise EvidenceError('DEX_TAX_RISK_UNKNOWN', observed=None, threshold=20)
    if not 0<=risk<20:
        raise EvidenceError('DEX_TAX_RISK_HIGH', observed=risk, threshold=20)
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
                if 'error' in cached[2]:
                    raise EvidenceError(cached[2]['error'],**copy.deepcopy(cached[2]['diagnostics']))
                return copy.deepcopy(cached[2])
            if self.clock()<self.blocked_until:
                raise ValueError(self.block_reason)
            permit=await self.budget.acquire()
            try:
                result=await self._token(chain,token,pool,other)
            except ValueError as exc:
                self.cache[key]=(self.clock(),15,dict(error=str(exc),diagnostics=copy.deepcopy(getattr(exc,'diagnostics',{}))))
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
                    raise EvidenceError(reason, provider='Honeypot.is',http_status=response.status,
                                        chain_id=chain,contract=token,pool=pool,other_contract=other,
                                        requested_at=start,retry_after_seconds=max(0,self.blocked_until-self.clock()))
                raw=await response.json()
            if not 0<=self.clock()-start<=6:
                raise ValueError('DEX_TAX_RESPONSE_STALE')
            return parse(raw,chain,token,pool,other,start)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError,asyncio.TimeoutError,TypeError,AttributeError,ValueError) as exc:
            reason=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('DEX_TAX_') else 'DEX_TAX_UNAVAILABLE'
            details=dict(getattr(exc,'diagnostics',{}))
            details.update(provider='Honeypot.is',chain_id=chain,contract=token,pool=pool,
                           other_contract=other,requested_at=start,error_type=type(exc).__name__)
            raise EvidenceError(reason,**details) from None

    async def route(self, quote):
        chain=quote['chain_id']
        sell,buy=quote['sell_token'].lower(),quote['buy_token'].lower()
        paths=quote.get('route')
        if not isinstance(paths,list) or not 1<=len(paths)<=4:
            raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
        scopes=[]
        # Validate all paths BEFORE issuing evidence requests. Every intermediate
        # token must be safe in both of its actual pools; endpoint evidence alone
        # cannot authorize a composed route. Hooks and RFQs remain unsupported.
        for path in paths:
            if not isinstance(path,list) or not 1<=len(path)<=3:
                raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
            previous=sell;visited={sell}
            for hop in path:
                if not isinstance(hop,dict):raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
                pool=hop.get('pool');a=str(hop.get('tokenIn','')).lower();b=str(hop.get('tokenOut','')).lower()
                if (hop.get('exchange') not in EXCHANGES or not isinstance(pool,str) or not ADDRESS.fullmatch(pool)
                        or not ADDRESS.fullmatch(a) or not ADDRESS.fullmatch(b) or a!=previous or b in visited):
                    raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
                scopes.append((pool.lower(),a,b));previous=b;visited.add(b)
            if previous!=buy:
                raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
        if len(scopes)>6:raise ValueError('DEX_TAX_ROUTE_UNSUPPORTED')
        rows={side:[] for side in ('sellToken','buyToken')}
        for pool,a,b in dict.fromkeys(scopes):
            for token,other in ((a,b),(b,a)):
                row=await self.token(chain,token,pool,other)
                if not 0<=self.clock()-row['observed_at']<60:
                    raise ValueError('DEX_TAX_EVIDENCE_STALE')
                if any(Decimal(row[f])!=0 for f in ('buyTaxBps','sellTaxBps','transferTaxBps')):
                    raise EvidenceError('DEX_VIRTUAL_TOKEN_TAX_UNSUPPORTED',contract=token,pool=pool,
                                        observed={f:row[f] for f in ('buyTaxBps','sellTaxBps','transferTaxBps')},threshold=0)
                if token==sell:rows['sellToken'].append(row)
                if token==buy:rows['buyToken'].append(row)
        metadata={}
        for side,evidence in rows.items():
            metadata[side]={field:str(max(Decimal(row[field]) for row in evidence))
                            for field in ('buyTaxBps','sellTaxBps','transferTaxBps')}
            metadata[side]['evidence']=evidence
        metadata['route_evidence']=dict(paths=len(paths),hops=len(scopes),all_hops_zero_tax=True,
                                        execution_verified=False)
        return metadata
