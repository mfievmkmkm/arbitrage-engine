"""KyberSwap GET routes only. No builds, approvals, wallet or order writes.

Contract: https://api-docs.kyberswap.com/aggregator-api.html
Public admission stays below 3 requests/second, shared by scans and exits.
Optional pool-scoped simulations supply PAPER tax evidence, never execution proof.
"""
import asyncio
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import aiohttp
from .zerox_research import Provider as MetadataProvider, ADDRESS
from .dex_request_budget import Budget

CHAINS = {1:'ethereum', 10:'optimism', 56:'bsc', 137:'polygon',
          42161:'arbitrum', 8453:'base', 43114:'avalanche'}


def decimal(value, positive=False):
    if value is None or isinstance(value, bool) or str(value).strip() == '':
        raise ValueError('KYBER_COST_UNKNOWN')
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('KYBER_COST_INVALID')
    return result


class Provider(MetadataProvider):
    name = 'KyberSwap'

    def __init__(self, metadata, session=None, clock=None, tax_reader=None):
        super().__init__(None, session, **({'clock':clock} if clock else {}))
        self.metadata = metadata
        self.tax_reader = tax_reader
        self.budget = Budget(spacing=.4, concurrency=4)
        self.server_failures={}

    async def close(self):
        if self.tax_reader:
            await self.tax_reader.close()
        await super().close()

    async def price(self, *args, **kwargs):
        # Cold simulations can age a quote. Requote once through the same budget;
        # verify the returned path again rather than reusing unrelated evidence.
        for attempt in range(2):
            q = await super().price(*args, **kwargs)
            if not q.get('ok') or not self.tax_reader:return q
            try:
                q["token_metadata"] = await self.tax_reader.route(q)
                q["tax_evidence"] = "POOL_SIMULATION_PAPER_ONLY"
            except ValueError as exc:
                q["tax_reason"] = str(exc)
                q["tax_diagnostics"] = getattr(exc,'diagnostics',{})
                return q
            if 0<=self.clock()-q['ts']<=2:return q
        # Two seconds triggers refresh; it is NOT a new admission limit.
        # Keep the established 15s DEX ceiling and strategy-specific tighter
        # checks (e.g. B's 3s) instead of rejecting a valid refreshed quote.
        if not 0<=self.clock()-q['ts']<=15:
            q['tax_reason']='DEX_TAX_QUOTE_REFRESH_REQUIRED'
            q['tax_diagnostics']=dict(observed=self.clock()-q['ts'],threshold=15,unit='seconds')
        return q

    async def token(self, network, address):
        return await self.metadata.token(network, address)

    def health(self):
        return dict(super().health(), provider=self.name)

    async def _price(self, chain_id, sell_token, buy_token, sell_amount, slippage_bps=None):
        try:
            if type(chain_id) is not int or chain_id not in CHAINS:
                raise ValueError('KYBER_CHAIN_UNSUPPORTED')
            if self.tax_reader and chain_id not in (1,56,8453):
                raise ValueError('DEX_TAX_CHAIN_UNSUPPORTED')
            if not all(isinstance(t,str) and ADDRESS.fullmatch(t) for t in (sell_token,buy_token)) or sell_token.lower()==buy_token.lower():
                raise ValueError('TOKEN_ADDRESSES_INVALID')
            if not isinstance(sell_amount,str) or not sell_amount.isdigit() or int(sell_amount)<=0:
                raise ValueError('DEX_AMOUNT_OR_CHAIN_INVALID')
            if slippage_bps is not None and (type(slippage_bps) is not int or not 0<=slippage_bps<=100):
                raise ValueError('DEX_SLIPPAGE_INVALID')
            now = self.clock()
            health = self.health()
            until, reason = self.chain_cooldowns.get(chain_id, (0,None))
            if health['blocked'] or until>now:
                return dict(ok=False,reason=health['reason'] or reason,request_sent=False,
                            retry_after_seconds=max(health['retry_after_seconds'],until-now))
            await self.start()
            started = self.clock()
            self.requests_sent += 1
            self.last_request_at = started
            params=dict(tokenIn=sell_token,tokenOut=buy_token,amountIn=sell_amount,gasInclude='true')
            # Supported simulation protocols only when PAPER tax checks are active.
            if self.tax_reader and chain_id in (1,8453):
                params['includedSources']='uniswap,uniswapv3'
            elif self.tax_reader and chain_id==56:
                params['includedSources']='pancake,pancake-v3'
            if self.tax_reader:
                params.update(onlyDirectPools='false', onlySinglePath='true')
            async with self.session.get(
                f'https://aggregator-api.kyberswap.com/{CHAINS[chain_id]}/api/v1/routes',
                params=params,
                headers={'X-Client-Id':'arbitrage-engine-paper'}) as response:
                if response.status != 200:
                    status = response.status
                    reason = 'DEX_HTTP_'+str(status)
                    problem_code=None
                    response_evidence={}
                    if status==400:
                        # Retain only recognized codes; never persist response text.
                        try:
                            error=await response.json()
                            if isinstance(error,dict) and type(error.get('code')) is int:
                                problem_code=error['code']
                                if problem_code==40011:reason='KYBER_FILTERED_LIQUIDITY'
                                message=error.get('message')
                                if isinstance(message,str):
                                    known={'route not found':'ROUTE_NOT_FOUND','no route found':'ROUTE_NOT_FOUND',
                                           'no eligible pools':'NO_ELIGIBLE_POOLS','invalid amount':'INVALID_AMOUNT',
                                           'invalid token address':'INVALID_ADDRESS','insufficient liquidity':'INSUFFICIENT_LIQUIDITY'}
                                    response_evidence=dict(message_category=known.get(message.strip().lower(),'UNCLASSIFIED'),
                                        message_sha256=hashlib.sha256(message.encode()).hexdigest(),message_length=len(message))
                        except (ValueError,aiohttp.ClientError):pass

                    delay = 0
                    if status in (401,403):
                        self.access_failures += 1
                        delay = min(1800,30*2**min(6,self.access_failures-1))
                        self.access_block_until = self.clock()+delay
                        self.access_status = status
                        self.access_error_code = 'KYBER_HTTP_'+str(status)
                    elif status == 429:
                        delay = 30
                        try:
                            retry = decimal(getattr(response,'headers',{}).get('Retry-After',30))
                            delay = float(max(Decimal(1),min(Decimal(300),retry)))
                        except (ValueError,InvalidOperation):
                            pass
                        self.rate_limit_until = self.clock()+delay
                    elif status>=500:
                        self.server_failures[chain_id]=self.server_failures.get(chain_id,0)+1
                        delay = min(60,5*2**min(4,self.server_failures[chain_id]-1))
                        self.chain_cooldowns[chain_id] = (self.clock()+delay,reason)
                    return dict(ok=False,reason=reason,request_sent=True,retry_after_seconds=delay,
                                error_code=self.access_error_code if status in (401,403) else problem_code,
                                provider=self.name,http_status=status,chain_id=chain_id,response_evidence=response_evidence,
                                request_scope=dict(sell_contract=sell_token,buy_contract=buy_token,amount_raw=sell_amount,
                                                   direct_only=False,sources=params.get('includedSources'),requested_at=started))
                raw = await response.json()
            self.access_failures = 0
            self.server_failures[chain_id]=0
            self.access_block_until = 0
            self.access_status = self.access_error_code = None
            if not 0<=self.clock()-started<=8:
                raise ValueError('KYBER_RESPONSE_STALE')
            if not isinstance(raw,dict) or type(raw.get('code')) is not int or raw['code']!=0:
                raise ValueError('KYBER_ROUTE_UNAVAILABLE')
            q = (raw.get('data') or {}).get('routeSummary')
            if not isinstance(q,dict):
                raise ValueError('KYBER_RESPONSE_INVALID')
            if (str(q.get('tokenIn','')).lower(),str(q.get('tokenOut','')).lower(),q.get('amountIn')) != (sell_token.lower(),buy_token.lower(),sell_amount):
                raise ValueError('KYBER_RESPONSE_SCOPE_MISMATCH')
            out = q.get('amountOut')
            if not isinstance(out,str) or not out.isdigit() or int(out)<=0:
                raise ValueError('KYBER_AMOUNT_INVALID')
            stamp = q.get('timestamp')
            if type(stamp) not in (int,float) or not -2<=self.clock()-stamp<=15:
                raise ValueError('KYBER_ROUTE_STALE')
            if not isinstance(q.get('route'),list) or not q['route']:
                raise ValueError('KYBER_ROUTE_UNAVAILABLE')
            # Fee-bearing routes need separate accounting support; never hide fees.
            extra = q.get('extraFee')
            if not isinstance(extra,dict) or extra.get('feeAmount') not in ('','0',0):
                raise ValueError('KYBER_EXTRA_FEE_UNSUPPORTED')
            gas = decimal(q.get('gas'),True)*decimal(q.get('gasPrice'),True)
            l1 = decimal(q.get('l1FeeUsd'))
            return dict(ok=True,provider=self.name,chain_id=chain_id,
                        sell_token=sell_token,buy_token=buy_token,sell_amount_raw=sell_amount,
                        buy_amount_raw=out,network_fee_raw=str(gas.to_integral_value(rounding=ROUND_CEILING)),
                        network_fee_usd_extra=str(l1),gas_price_raw=str(decimal(q.get('gasPrice'),True)),token_metadata=None,
                        tax_evidence='UNKNOWN',route=q['route'],block_number=None,
                        route_timestamp=stamp,ts=started,mode='RESEARCH_INDICATIVE',
                        paper_allowed=False,live_allowed=False)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,TypeError,AttributeError,InvalidOperation) as exc:
            # Error text is restricted to our own constants, never a response body.
            reason = str(exc) if isinstance(exc,ValueError) and str(exc).startswith(('KYBER_','TOKEN_','DEX_')) else 'KYBER_PRICE_UNAVAILABLE'
            return dict(ok=False,reason=reason)
