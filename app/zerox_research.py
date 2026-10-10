"""Read-only indicative prices. No approvals, signing, quotes for execution or transactions.
API contract: https://docs.0x.org/api-reference/evm-ap-is/swap/allowanceholder-getprice
"""

import asyncio
import time
import re
import aiohttp

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class Provider:
    def __init__(self, api_key, session=None):
        self.api_key = api_key
        self.session = session
        self.owned = session is None

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

    async def close(self):
        if self.owned and self.session:
            await self.session.close()

    async def price(self, chain_id, sell_token, buy_token, sell_amount):
        if not self.api_key:
            return {"ok": False, "reason": "DEX_API_KEY_MISSING"}
        if (
            not ADDRESS.fullmatch(sell_token)
            or not ADDRESS.fullmatch(buy_token)
            or sell_token.lower() == buy_token.lower()
        ):
            return {"ok": False, "reason": "TOKEN_ADDRESSES_INVALID"}
        try:
            amount = int(str(sell_amount))
            chain = int(chain_id)
            if amount <= 0 or chain <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return {"ok": False, "reason": "DEX_AMOUNT_OR_CHAIN_INVALID"}
        await self.start()
        params = {
            "chainId": str(chain),
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": str(amount),
        }
        headers = {"0x-api-key": self.api_key, "0x-version": "v2"}
        try:
            async with self.session.get(
                "https://api.0x.org/swap/allowance-holder/price",
                params=params,
                headers=headers,
            ) as response:
                if response.status != 200:
                    return {"ok": False, "reason": "DEX_HTTP_" + str(response.status)}
                raw = await response.json()
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
                "route": raw.get("route"),
                "block_number": raw.get("blockNumber"),
                "ts": time.time(),
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
