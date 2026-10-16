"""Read-only indicative prices. No approvals, signing, quotes for execution or transactions.
API contract: https://docs.0x.org/api-reference/evm-ap-is/swap/allowanceholder-getprice
"""

import asyncio
import time
import re
import math
import aiohttp

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class Provider:
    def __init__(self, api_key, session=None, clock=time.time):
        self.api_key = api_key
        self.session = session
        self.owned = session is None
        self.clock = clock
        self.chain_cooldowns = {}
        self.rate_limit_until = 0
        self.access_block_until = 0
        self.access_error_code = None
        self.access_status = None

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

    async def close(self):
        if self.owned and self.session:
            await self.session.close()

    async def token(self, network, address):
        if not ADDRESS.fullmatch(address):
            raise ValueError("TOKEN_ADDRESSES_INVALID")
        await self.start()
        async with self.session.get(f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}/info",
                                    headers={"Accept":"application/json"}) as response:
            if response.status != 200:
                raise ValueError("Метаданные токена недоступны: Gecko HTTP " + str(response.status))
            raw = await response.json()
        data = raw.get("data", {})
        attr = data.get("attributes", {})
        if data.get("id", "").lower() != (network+"_"+address).lower() or attr.get("address", "").lower() != address.lower():
            raise ValueError("EVM_TOKEN_METADATA_SCOPE_MISMATCH")
        decimals = attr.get("decimals")
        if type(decimals) is not int or not 0 <= decimals <= 36 or not isinstance(attr.get("symbol"), str):
            raise ValueError("EVM_TOKEN_METADATA_INVALID")
        return dict(symbol=attr["symbol"], decimals=decimals)

    async def price(self, chain_id, sell_token, buy_token, sell_amount, slippage_bps=None):
        if not self.api_key:
            return {"ok": False, "reason": "DEX_API_KEY_MISSING"}
        if (
            not isinstance(sell_token, str) or not isinstance(buy_token, str)
            or not ADDRESS.fullmatch(sell_token)
            or not ADDRESS.fullmatch(buy_token)
            or sell_token.lower() == buy_token.lower()
        ):
            return {"ok": False, "reason": "TOKEN_ADDRESSES_INVALID"}
        try:
            amount = int(str(sell_amount))
            chain = int(chain_id)
            if isinstance(chain_id, bool) or amount <= 0 or chain <= 0 or str(chain_id) != str(chain):
                raise ValueError
        except (ValueError, TypeError):
            return {"ok": False, "reason": "DEX_AMOUNT_OR_CHAIN_INVALID"}
        params = {
            "chainId": str(chain),
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": str(amount),
        }
        if slippage_bps is not None:
            if type(slippage_bps) is not int or not 0 <= slippage_bps <= 100:
                return {"ok": False, "reason": "DEX_SLIPPAGE_INVALID"}
            params["slippageBps"] = str(slippage_bps)
        now = self.clock()
        if self.access_block_until > now:
            return dict(ok=False, reason='DEX_HTTP_' + str(self.access_status),
                        error_code=self.access_error_code, retry_after_seconds=self.access_block_until-now,
                        request_sent=False, action_required='CHECK_API_ACCESS')
        blocked_until, previous_reason = self.chain_cooldowns.get(chain, (0, None))
        if self.rate_limit_until > now:
            blocked_until, previous_reason = self.rate_limit_until, 'DEX_HTTP_429'
        if blocked_until > now:
            return dict(ok=False, reason=previous_reason,
                        retry_after_seconds=blocked_until-now, request_sent=False)
        await self.start()
        headers = {"0x-api-key": self.api_key, "0x-version": "v2"}
        started = self.clock()
        try:
            async with self.session.get(
                "https://api.0x.org/swap/allowance-holder/price",
                params=params,
                headers=headers,
            ) as response:
                if response.status != 200:
                    reason = 'DEX_HTTP_' + str(response.status)
                    delay = 0
                    code = None
                    if response.status in (401, 403):
                        try:
                            problem = await response.json()
                            candidate = problem.get('code') if isinstance(problem, dict) else None
                            if candidate in ('TAKER_NOT_AUTHORIZED_FOR_TRADE', 'UNAUTHORIZED', 'FORBIDDEN'):
                                code = candidate
                        except (aiohttp.ClientError, ValueError, TypeError):
                            pass
                    if response.status in (401, 403, 429) or response.status >= 500:
                        delay = 30 if response.status in (401, 403, 429) else 5
                        if response.status == 429:
                            try:
                                retry = float(getattr(response, 'headers', {}).get('Retry-After', delay))
                                if math.isfinite(retry):
                                    delay = min(60, max(1, retry))
                            except (ValueError, TypeError):
                                pass
                            self.rate_limit_until = self.clock() + delay
                        elif response.status in (401, 403):
                            self.access_block_until = self.clock() + delay
                            self.access_error_code = code or ('UNAUTHORIZED' if response.status == 401 else 'HTTP_403_UNKNOWN')
                            self.access_status = response.status
                        else:
                            self.chain_cooldowns[chain] = (self.clock() + delay, reason)
                    return dict(ok=False, reason=reason, retry_after_seconds=delay,
                                error_code=self.access_error_code if response.status in (401, 403) else code,
                                request_sent=True,
                                action_required='CHECK_API_ACCESS' if response.status in (401, 403) else None)
                raw = await response.json()
            if not 0 <= self.clock() - started <= 8:
                return dict(ok=False, reason='DEX_PRICE_RESPONSE_STALE')
            if not isinstance(raw, dict):
                return dict(ok=False, reason='DEX_PRICE_RESPONSE_INVALID')
            if raw.get("liquidityAvailable") is not True:
                return {"ok": False, "reason": "DEX_LIQUIDITY_UNAVAILABLE"}
            if (
                str(raw.get("sellToken", "")).lower() != sell_token.lower()
                or str(raw.get("buyToken", "")).lower() != buy_token.lower()
            ):
                return {"ok": False, "reason": "DEX_TOKEN_RESPONSE_MISMATCH"}
            if (
                int(raw.get("sellAmount") or 0) != amount
                or int(raw.get("buyAmount") or 0) <= 0
            ):
                return {"ok": False, "reason": "DEX_AMOUNT_RESPONSE_INVALID"}
            # Explicitly preserve unknown costs. An indicative API price cannot prove execution safety.
            return {
                "ok": True,
                "provider": "0x",
                "chain_id": chain,
                "sell_token": sell_token,
                "buy_token": buy_token,
                "sell_amount_raw": str(amount),
                "buy_amount_raw": str(raw["buyAmount"]),
                "network_fee_raw": raw.get("totalNetworkFee"),
                "min_buy_amount_raw": raw.get("minBuyAmount"),
                "token_metadata": raw.get("tokenMetadata"),
                "route": raw.get("route"),
                "block_number": raw.get("blockNumber"),
                "ts": started,
                "mode": "RESEARCH_INDICATIVE",
                "paper_allowed": False,
                "live_allowed": False,
            }
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            return {"ok": False, "reason": "DEX_PRICE_UNAVAILABLE"}


class Cycle:
    def __init__(self, provider, routes):
        self.provider = provider
        self.routes = routes
        self.index = 0

    async def cycle(self):
        if not self.routes:
            return []
        # One route per cycle keeps request use bounded.
        route = self.routes[self.index % len(self.routes)]
        self.index += 1
        result = await self.provider.price(
            route["chain_id"],
            route["sell_token"],
            route["buy_token"],
            route["sell_amount_raw"],
        )
        return [
            {
                "strategy": "cex_dex",
                "symbol": route.get("label", "DEX PRICE"),
                "quote": result,
                "ts": time.time(),
                "evidence_mode": "RESEARCH_INDICATIVE",
                "paper_allowed": False,
                "live_allowed": False,
            }
        ]
