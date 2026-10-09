"""Read-only 0x firm quote + chain-bound RPC simulation + native CEX hedge.

No approval, signer, private key, transaction broadcast or live wallet authority.
A quote's API simulation flag alone is not execution proof. Contract identity
and call targets come from an expiring operator registry; RPC checks decimals,
bytecode, balance, block identity and eth_call. Profit is a convergence ceiling
model, not a booked Paper/live result.

Contracts: https://docs.0x.org/api-reference/evm-ap-is/swap/allowanceholder-getquote
https://docs.0x.org/docs/introduction/api-issues
"""

import asyncio
import hashlib
import json
import math
import re
import time
from decimal import Decimal
import aiohttp
from .zerox_research import Provider as ResearchProvider, ADDRESS
from .native_order_plan import number, market
from .spot_future_native_plan import hedge, rate
from .spot_future_live_preflight import quote as cex_quote
from .public_books import normalize

RAW = re.compile(r"^(0|[1-9][0-9]*)$")
HEX = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
ZERO = "0x" + "0" * 40
NATIVE = "0x" + "e" * 40


def integer(value, name, positive=True):
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not RAW.fullmatch(str(value))
    ):
        raise ValueError(name + "_INVALID")
    value = int(value)
    if value < (1 if positive else 0) or value >= 2**256:
        raise ValueError(name + "_INVALID")
    return value


def address(value):
    if (
        not isinstance(value, str)
        or not ADDRESS.fullmatch(value)
        or value.lower() in (ZERO, NATIVE)
    ):
        raise ValueError("DEX_ADDRESS_INVALID")
    return value.lower()


def registry_scope(registry, chain, sell, buy, now):
    if (
        not isinstance(registry, dict)
        or type(registry.get("version")) is not int
        or registry.get("version") != 1
    ):
        raise ValueError("DEX_REGISTRY_REQUIRED")
    created, expires = registry.get("verified_at"), registry.get("expires_at")
    if (
        any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            for x in (created, expires)
        )
        or not created <= now < expires
        or expires - created > 86400
    ):
        raise ValueError("DEX_REGISTRY_EXPIRED")
    if (
        not isinstance(registry.get("evidence_id"), str)
        or not registry["evidence_id"].strip()
    ):
        raise ValueError("DEX_REGISTRY_EVIDENCE_REQUIRED")
    scope = registry.get("chains", {}).get(str(chain), {})
    tokens = scope.get("tokens", {})
    for token in (sell, buy):
        row = tokens.get(token)
        if (
            not isinstance(row, dict)
            or row.get("contract_verified") is not True
            or type(row.get("decimals")) is not int
            or not 0 <= row["decimals"] <= 36
        ):
            raise ValueError("DEX_TOKEN_IDENTITY_UNVERIFIED")
    stable = [t for t in (sell, buy) if tokens[t].get("quote_usdt") is True]
    if len(stable) != 1:
        raise ValueError("DEX_USDT_QUOTE_IDENTITY_REQUIRED")
    asset = buy if stable[0] == sell else sell
    row = tokens[asset]
    if not row.get("cex_venue") or not row.get("cex_symbol"):
        raise ValueError("DEX_CEX_ASSET_IDENTITY_REQUIRED")
    if scope.get("network_verified") is not True:
        raise ValueError("DEX_NETWORK_UNVERIFIED")
    targets = scope.get("transaction_targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("DEX_CALL_TARGET_ALLOWLIST_REQUIRED")
    for target in targets:
        address(target)
    return scope, tokens, asset, stable[0]


class Provider(ResearchProvider):
    def __init__(self, api_key, rpc_url, session=None, clock=time.time):
        super().__init__(api_key, session)
        if not isinstance(rpc_url, str) or not rpc_url.startswith("https://"):
            raise ValueError("DEX_HTTPS_RPC_REQUIRED")
        self.rpc_url, self.clock = rpc_url, clock
        self.counter = 0

    async def rpc(self, method, params):
        if method not in {
            "eth_chainId",
            "eth_getBlockByNumber",
            "eth_getCode",
            "eth_getBalance",
            "eth_call",
            "eth_gasPrice",
        }:
            raise ValueError("DEX_RPC_WRITE_FORBIDDEN")
        await self.start()
        self.counter += 1
        rid = self.counter
        async with self.session.post(
            self.rpc_url, json=dict(jsonrpc="2.0", id=rid, method=method, params=params)
        ) as r:
            if r.status != 200:
                raise ValueError("DEX_RPC_UNAVAILABLE")
            raw = await r.json()
        if (
            not isinstance(raw, dict)
            or raw.get("id") != rid
            or raw.get("error")
            or "result" not in raw
        ):
            raise ValueError("DEX_RPC_RESPONSE_UNVERIFIED")
        return raw["result"]

    async def firm(
        self,
        chain,
        sell,
        buy,
        amount,
        taker,
        registry,
        slippage_bps=20,
        *,
        exact_out=False,
    ):
        started = self.clock()
        try:
            chain = integer(chain, "DEX_CHAIN")
            amount = integer(amount, "DEX_AMOUNT")
            if type(exact_out) is not bool:
                raise ValueError("DEX_QUOTE_MODE_INVALID")
            sell, buy, taker = address(sell), address(buy), address(taker)
            scope, tokens, asset, stable = registry_scope(
                registry, chain, sell, buy, self.clock()
            )
            if type(slippage_bps) is not int or not 0 <= slippage_bps <= 100:
                raise ValueError("DEX_SLIPPAGE_LIMIT")
            if not self.api_key:
                raise ValueError("DEX_API_KEY_MISSING")
            await self.start()
            params = dict(
                chainId=str(chain),
                sellToken=sell,
                buyToken=buy,
                taker=taker,
                slippageBps=slippage_bps,
            )
            params["buyAmount" if exact_out else "sellAmount"] = str(amount)
            async with self.session.get(
                "https://api.0x.org/swap/allowance-holder/quote",
                params=params,
                headers={"0x-api-key": self.api_key, "0x-version": "v2"},
            ) as response:
                if response.status != 200:
                    raise ValueError("DEX_QUOTE_HTTP_" + str(response.status))
                raw = await response.json()
            if raw.get("liquidityAvailable") is not True:
                raise ValueError("DEX_LIQUIDITY_UNAVAILABLE")
            if (
                address(raw.get("sellToken")) != sell
                or address(raw.get("buyToken")) != buy
            ):
                raise ValueError("DEX_QUOTE_SCOPE_MISMATCH")
            bought = integer(raw.get("buyAmount"), "DEX_BUY_AMOUNT")
            requested = amount
            if exact_out:
                if bought != requested or raw.get("minBuyAmount") is not None:
                    raise ValueError("DEX_EXACT_OUT_SCOPE_MISMATCH")
                amount = integer(raw.get("maxSellAmount"), "DEX_MAX_SELL")
                if (
                    raw.get("sellAmount") is not None
                    and integer(raw["sellAmount"], "DEX_SELL_AMOUNT") > amount
                ):
                    raise ValueError("DEX_MAX_SELL_CONFLICT")
                minimum = bought
            else:
                if integer(raw.get("sellAmount"), "DEX_SELL_AMOUNT") != amount:
                    raise ValueError("DEX_QUOTE_SCOPE_MISMATCH")
                minimum = integer(raw.get("minBuyAmount"), "DEX_MIN_BUY")
                if (
                    minimum > bought
                    or minimum < bought * (10000 - slippage_bps) // 10000
                ):
                    raise ValueError("DEX_MIN_RECEIVED_CONFLICT")
            issues = raw.get("issues")
            if (
                not isinstance(issues, dict)
                or issues.get("simulationIncomplete") is not False
                or issues.get("allowance", True) is not None
                or issues.get("balance", True) is not None
                or issues.get("invalidSourcesPassed") != []
            ):
                raise ValueError("DEX_QUOTE_ISSUES_UNRESOLVED")
            metadata = raw.get("tokenMetadata") or {}
            for side in ("sellToken", "buyToken"):
                row = metadata.get(side) or {}
                for field in ("buyTaxBps", "sellTaxBps", "transferTaxBps"):
                    if integer(row.get(field), "DEX_TAX", positive=False) != 0:
                        raise ValueError("DEX_TOKEN_TAX_UNSUPPORTED")
            tx = raw.get("transaction")
            if not isinstance(tx, dict):
                raise ValueError("DEX_TRANSACTION_MISSING")
            target = address(tx.get("to"))
            if target not in {address(x) for x in scope["transaction_targets"]}:
                raise ValueError("DEX_CALL_TARGET_UNAPPROVED")
            data = tx.get("data")
            if (
                not isinstance(data, str)
                or not HEX.fullmatch(data)
                or len(data) < 10
                or len(data) > 131074
            ):
                raise ValueError("DEX_CALLDATA_INVALID")
            if integer(tx.get("value"), "DEX_NATIVE_VALUE", False) != 0:
                raise ValueError("DEX_NATIVE_VALUE_UNEXPECTED")
            gas = integer(tx.get("gas"), "DEX_GAS")
            price = integer(tx.get("gasPrice"), "DEX_GAS_PRICE")
            if gas > 8_000_000:
                raise ValueError("DEX_GAS_LIMIT")
            block = integer(raw.get("blockNumber"), "DEX_QUOTE_BLOCK")
            chain_rpc, latest, header, gas_now, native_balance = await asyncio.gather(
                self.rpc("eth_chainId", []),
                self.rpc("eth_getBlockByNumber", ["latest", False]),
                self.rpc("eth_getBlockByNumber", [hex(block), False]),
                self.rpc("eth_gasPrice", []),
                self.rpc("eth_getBalance", [taker, hex(block)]),
            )
            if int(chain_rpc, 16) != chain:
                raise ValueError("DEX_RPC_CHAIN_MISMATCH")
            if (
                not isinstance(header, dict)
                or not isinstance(latest, dict)
                or not 0 <= int(latest["number"], 16) - block <= 2
                or int(header["number"], 16) != block
            ):
                raise ValueError("DEX_BLOCK_STALE")
            block_ts = int(header["timestamp"], 16)
            if not 0 <= self.clock() - block_ts <= 30:
                raise ValueError("DEX_BLOCK_STALE")
            block_hash = header.get("hash")
            if not isinstance(block_hash, str) or not re.fullmatch(
                r"0x[0-9a-fA-F]{64}", block_hash
            ):
                raise ValueError("DEX_BLOCK_HASH_UNKNOWN")
            if int(gas_now, 16) > price:
                raise ValueError("DEX_GAS_SPIKE")
            network = integer(raw.get("totalNetworkFee"), "DEX_NETWORK_FEE")
            fee = max(network, gas * price)
            if int(native_balance, 16) < fee:
                raise ValueError("DEX_WALLET_GAS_LOW")

            async def token(token):
                code, decimals = await asyncio.gather(
                    self.rpc("eth_getCode", [token, hex(block)]),
                    self.rpc(
                        "eth_call", [dict(to=token, data="0x313ce567"), hex(block)]
                    ),
                )
                if (
                    not isinstance(code, str)
                    or not HEX.fullmatch(code)
                    or code in ("0x", "0x00")
                ):
                    raise ValueError("DEX_TOKEN_CODE_MISSING")
                if (
                    not isinstance(decimals, str)
                    or not re.fullmatch(r"0x[0-9a-fA-F]{64}", decimals)
                    or int(decimals, 16) != tokens[token]["decimals"]
                ):
                    raise ValueError("DEX_TOKEN_DECIMALS_MISMATCH")

            await asyncio.gather(*(token(t) for t in (sell, buy)))
            balance = await self.rpc(
                "eth_call",
                [
                    dict(to=sell, data="0x70a08231" + taker[2:].rjust(64, "0")),
                    hex(block),
                ],
            )
            if (
                not isinstance(balance, str)
                or not re.fullmatch(r"0x[0-9a-fA-F]{64}", balance)
                or int(balance, 16) < amount
            ):
                raise ValueError("DEX_WALLET_TOKEN_BALANCE_LOW")
            result = await self.rpc(
                "eth_call",
                [
                    dict(
                        to=target,
                        data=data,
                        **{"from": taker},
                        gas=hex(gas),
                        gasPrice=hex(price),
                        value="0x0",
                    ),
                    hex(block),
                ],
            )
            if not isinstance(result, str) or not HEX.fullmatch(result):
                raise ValueError("DEX_SIMULATION_RESPONSE_INVALID")
            final = await self.rpc("eth_getBlockByNumber", [hex(block), False])
            if not isinstance(final, dict) or final.get("hash") != block_hash:
                raise ValueError("DEX_BLOCK_REORG")
            if not 0 <= self.clock() - started <= 15:
                raise ValueError("DEX_QUOTE_STALE")
            # Calldata/RPC URL/taker/key are not journal payloads or signing instructions.
            fingerprint = hashlib.sha256(
                json.dumps(
                    dict(
                        chain=chain,
                        block_hash=block_hash,
                        target=target,
                        data=data,
                        sell=sell,
                        buy=buy,
                        amount=amount,
                        minimum=minimum,
                        quote_mode="exact_out" if exact_out else "exact_in",
                        requested=requested,
                    ),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            return dict(
                ok=True,
                chain_id=chain,
                sell_token=sell,
                buy_token=buy,
                sell_amount_raw=str(amount),
                buy_amount_raw=str(bought),
                min_buy_amount_raw=str(minimum),
                max_sell_amount_raw=str(amount) if exact_out else None,
                quote_mode="exact_out" if exact_out else "exact_in",
                network_fee_raw=str(fee),
                block_number=block,
                block_hash=block_hash,
                ts=started,
                received_at=self.clock(),
                quote_fingerprint=fingerprint,
                asset=asset,
                quote_token=stable,
                simulation_verified=True,
                mode="READ_ONLY_FIRM_SIMULATION",
                live_allowed=False,
                paper_allowed=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return dict(
                ok=False,
                reason=(
                    str(e)
                    if isinstance(e, ValueError)
                    else "DEX_SIMULATION_UNAVAILABLE"
                ),
                mode="READ_ONLY_FIRM_SIMULATION",
                live_allowed=False,
                paper_allowed=False,
            )


class Cycle:
    def __init__(
        self, provider, routes, registry, public, private=None, clock=time.time
    ):
        (
            self.provider,
            self.routes,
            self.registry,
            self.public,
            self.private,
            self.clock,
        ) = (provider, routes, registry, public, private or {}, clock)
        self.index = 0

    async def cycle(self):
        if not self.routes:
            return []
        route = self.routes[self.index % len(self.routes)]
        self.index += 1
        return [await self.observe(route)]

    async def observe(self, route):
        q = await self.provider.firm(
            route["chain_id"],
            route["sell_token"],
            route["buy_token"],
            route["sell_amount_raw"],
            route["taker"],
            self.registry,
        )
        row = dict(
            strategy="cex_dex",
            symbol=route.get("label", "DEX SIMULATION"),
            quote=q,
            ts=self.clock(),
            evidence_mode="READ_ONLY_FIRM_SIMULATION",
            live_allowed=False,
            paper_allowed=False,
        )
        if not q["ok"]:
            return row
        try:
            scope, tokens, asset, stable = registry_scope(
                self.registry,
                q["chain_id"],
                q["sell_token"],
                q["buy_token"],
                self.clock(),
            )
            identity = tokens[asset]
            v = identity["cex_venue"]
            symbol = identity["cex_symbol"]
            c = self.public[v]
            m = market(c, symbol)
            if m["base"] != identity.get("base"):
                raise ValueError("DEX_CEX_BASE_IDENTITY_MISMATCH")
            fees = self.private.get(v)
            if not fees or fees.has.get("fetchTradingFee") is not True:
                raise ValueError("DEX_CEX_ACCOUNT_FEE_UNKNOWN")
            fee = await asyncio.wait_for(fees.fetch_trading_fee(symbol), 8)
            if fee.get("symbol") != symbol:
                raise ValueError("DEX_CEX_FEE_SCOPE_MISMATCH")
            fee = float(rate(fee.get("taker")))
            # Native-gas valuation uses a separate fresh executable CEX ask.
            start = self.clock()
            raw = await asyncio.wait_for(
                c.fetch_order_book(scope["native_symbol"], limit=20), 8
            )
            gas_book = normalize(raw, scope["native_symbol"], start, self.clock(), 1.5)
            native_price = float(number(gas_book["asks"][0][0], "DEX_NATIVE_PRICE"))
            decimals = integer(scope["native_decimals"], "DEX_NATIVE_DECIMALS", False)
            if decimals > 36:
                raise ValueError("DEX_NATIVE_DECIMALS_INVALID")
            gas_usd = float(
                Decimal(q["network_fee_raw"])
                / Decimal(10**decimals)
                * Decimal(str(native_price))
            )
            if not math.isfinite(gas_usd) or gas_usd > 0.25:
                raise ValueError("DEX_GAS_BUDGET_LIMIT")
            forward = q["sell_token"] == stable
            base_raw = q["min_buy_amount_raw"] if forward else q["sell_amount_raw"]
            base = float(Decimal(base_raw) / Decimal(10 ** tokens[asset]["decimals"]))
            raw = await asyncio.wait_for(c.fetch_order_book(symbol, limit=20), 8)
            reference = raw["bids" if forward else "asks"][0][0]
            native, hedged = hedge(c, symbol, base, reference, reduce_only=not forward)
            if not forward:
                from dataclasses import replace

                native = replace(native, reduce_only=False)
            native = await cex_quote(
                c, v, native, float(m["contractSize"]), clock=self.clock
            )
            amount = float(
                Decimal(q["sell_amount_raw"] if forward else q["min_buy_amount_raw"])
                / Decimal(10 ** tokens[stable]["decimals"])
            )
            notional = hedged * native.price
            if max(amount, notional) > 5 or abs(base - hedged) * native.price > 0.05:
                raise ValueError("DEX_MICRO_NOTIONAL_OR_RESIDUAL_LIMIT")
            ceiling = (
                (notional - amount if forward else amount - notional)
                - 2 * notional * fee
                - 2 * gas_usd
                - max(amount, notional) * 0.001
            )
            if (
                self.clock() - q["ts"] > 15
                or self.clock() - gas_book["timestamp"] / 1000 > 1.5
            ):
                raise ValueError("DEX_CEX_PAIRED_EVIDENCE_STALE")
            row.update(
                simulation_allowed=True,
                net_ceiling_model=ceiling,
                notional=notional,
                gas_usd=gas_usd,
                gas_price=native_price,
                gas_book=gas_book,
                native_decimals=decimals,
                cex_venue=v,
                cex_symbol=symbol,
                cex_side=native.side,
                cex_contracts=native.qty,
                base_qty=hedged,
                entry_price=native.price,
                contract_size=float(m["contractSize"]),
                cex_evidence=native.market_evidence,
                fee_rate=fee,
                dex_cash=amount,
                asset_amount_raw=str(base_raw),
                asset_decimals=tokens[asset]["decimals"],
                stable_decimals=tokens[stable]["decimals"],
                forward=forward,
                reason=(
                    "SIMULATED_NET_POSITIVE"
                    if ceiling > 0.05
                    else "SIMULATED_NET_BELOW_THRESHOLD"
                ),
                cost_model="FULL_CONVERGENCE_CEILING_WITH_DOUBLE_GAS_AND_EXIT_FEES_NOT_REALIZED",
                hypothetical_edge=ceiling / max(amount, notional) * 100,
            )
        except Exception as e:
            row.update(
                simulation_allowed=False,
                reason=(
                    str(e)
                    if isinstance(e, ValueError)
                    else "DEX_CEX_SIMULATION_UNAVAILABLE"
                ),
            )
        return row
