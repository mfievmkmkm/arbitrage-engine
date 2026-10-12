"""Quote-only Jupiter V2. Never sends a taker, signs or calls /execute.

Contract: https://developers.jup.ag/docs/api-reference/swap/order
Solana support intentionally limited to CEX-only reference experiments.
"""

import asyncio
import time
from decimal import Decimal

import aiohttp

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def mint_valid(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    try:
        n = 0
        for c in value:
            n = n * 58 + ALPHABET.index(c)
        size = (n.bit_length() + 7) // 8
        return size + len(value) - len(value.lstrip("1")) == 32
    except ValueError:
        return False


class Provider:
    def __init__(self, api_key, session=None, clock=time.time):
        self.api_key, self.session, self.clock = api_key, session, clock
        self.owned = session is None
        self.lock = asyncio.Lock()
        self.next_request = 0

    async def start(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

    async def close(self):
        if self.owned and self.session:
            await self.session.close()

    async def get(self, path, params):
        if not self.api_key:
            raise ValueError("JUPITER_API_KEY_MISSING")
        await self.start()
        async with self.lock:
            delay = max(0, self.next_request - time.monotonic())
            if delay > 1.1:
                # A rate-limit cooldown must not delay CEX-only risk exits.
                raise ValueError("JUPITER_RATE_LIMIT_COOLDOWN")
            await asyncio.sleep(delay)
            self.next_request = time.monotonic() + 1
            async with self.session.get("https://api.jup.ag/" + path, params=params,
                                        headers={"x-api-key": self.api_key}) as response:
                if response.status == 429:
                    self.next_request = time.monotonic() + 30
                if response.status != 200:
                    raise ValueError("JUPITER_HTTP_" + str(response.status))
                return await response.json()

    async def token(self, mint):
        if not mint_valid(mint):
            raise ValueError("SOLANA_MINT_INVALID")
        data = await self.get("tokens/v2/search", {"query": mint})
        matches = [x for x in data if isinstance(x, dict) and x.get("id") == mint] if isinstance(data, list) else []
        if len(matches) != 1 or type(matches[0].get("decimals")) is not int or not 0 <= matches[0]["decimals"] <= 18:
            raise ValueError("JUPITER_TOKEN_METADATA_INVALID")
        # A symbol is diagnostic only. The configured mint is the identity.
        return matches[0]

    async def price(self, chain_id, sell, buy, amount, slippage_bps=20):
        try:
            if chain_id != "solana" or not all(mint_valid(x) for x in (sell, buy)) or sell == buy:
                raise ValueError("SOLANA_PRICE_SCOPE_INVALID")
            if not str(amount).isdigit() or not 0 < int(amount) <= 2**64 - 1:
                raise ValueError("SOLANA_PRICE_AMOUNT_INVALID")
            if type(slippage_bps) is not int or not 0 <= slippage_bps <= 100:
                raise ValueError("SOLANA_SLIPPAGE_INVALID")
            start = self.clock()
            raw = await self.get("swap/v2/order", dict(inputMint=sell, outputMint=buy,
                                amount=str(amount), swapMode="ExactIn", slippageBps=str(slippage_bps),
                                excludeRouters="jupiterz,dflow,okx"))
            if not isinstance(raw, dict) or raw.get("error") or raw.get("errorCode"):
                raise ValueError("JUPITER_QUOTE_UNAVAILABLE")
            if (raw.get("inputMint"), raw.get("outputMint"), str(raw.get("inAmount")), raw.get("swapMode")) != (sell, buy, str(amount), "ExactIn"):
                raise ValueError("JUPITER_RESPONSE_SCOPE_MISMATCH")
            if self.clock() - start > 8 or self.clock() < start:
                raise ValueError("JUPITER_RESPONSE_STALE")
            out, minimum = raw.get("outAmount"), raw.get("otherAmountThreshold")
            if not all(isinstance(x, str) and x.isdigit() and int(x) > 0 for x in (out, minimum)) or int(minimum) > int(out):
                raise ValueError("JUPITER_RESPONSE_AMOUNT_INVALID")
            # Reference experiments use an AMM quote with a declared route,
            # rather than accepting router-dependent RFQ response semantics.
            if raw.get("router") != "metis" or not isinstance(raw.get("routePlan"), list) or not raw["routePlan"]:
                raise ValueError("JUPITER_ROUTE_UNSUPPORTED")
            if type(raw.get("slippageBps")) not in (int, float) or raw["slippageBps"] != slippage_bps:
                raise ValueError("JUPITER_SLIPPAGE_MISMATCH")
            expected_minimum = int(Decimal(out) * (Decimal(1) - Decimal(slippage_bps) / 10000))
            if int(minimum) < expected_minimum:
                raise ValueError("JUPITER_MINIMUM_SLIPPAGE_MISMATCH")
            impact = Decimal(str(raw["priceImpact"])) / 100 if raw.get("priceImpact") is not None else Decimal(str(raw.get("priceImpactPct")))
            if not impact.is_finite() or abs(impact) > Decimal("0.01"):
                raise ValueError("JUPITER_PRICE_IMPACT_LIMIT")
            return dict(ok=True, provider="Jupiter V2", chain_id="solana", sell_token=sell,
                        buy_token=buy, sell_amount_raw=str(amount), buy_amount_raw=out,
                        min_buy_amount_raw=minimum, network_fee_raw=None, token_metadata=None,
                        price_impact_pct=str(impact), router="metis", route=raw["routePlan"], block_number=raw.get("contextSlot"),
                        ts=start, mode="RESEARCH_INDICATIVE", paper_allowed=False, live_allowed=False)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ArithmeticError, ValueError, TypeError, AttributeError) as exc:
            return dict(ok=False, reason=str(exc) if isinstance(exc, ValueError) else "JUPITER_PRICE_UNAVAILABLE")


class Router:
    """Dispatch routes without translating case-sensitive Solana mints."""
    def __init__(self, evm, solana):
        self.evm, self.solana = evm, solana

    async def price_route(self, r, sell_asset, amount):
        sell, buy = (r["asset"], r["stable"]) if sell_asset else (r["stable"], r["asset"])
        return await (self.solana if r["chain_id"] == "solana" else self.evm).price(
            r["chain_id"], sell, buy, str(amount), slippage_bps=int(r.get("slippage_bps", 20)))

    async def close(self):
        await self.solana.close()
