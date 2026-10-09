"""Read-only isolated-account admission and post-receipt hedge revalidation.

The NET here is a conservative full-convergence ceiling, not realized profit or
a promise of convergence. This component never signs, submits or certifies.
"""

import asyncio
import json
import math
import time
from decimal import localcontext
from pathlib import Path
import aiosqlite
from .dex_live_bridge import dec
from .dex_live_costs import USDT_MAINNET
from .dex_firm_simulation import registry_scope
from .live_acceptance import accepted
from .native_order_plan import market
from .private_reader import PrivateReader
from .public_books import normalize
from .recovery_market import executable
from .spot_future_native_plan import rate


class Admission:
    def __init__(
        self,
        path,
        backend,
        policy,
        registry_path,
        account_acceptance_path,
        gas_client,
        funding_service,
        reverse_quote,
        bankroll=50,
        minimum_net="0.05",
        hold_seconds=900,
        clock=time.time,
    ):
        self.path, self.backend, self.policy = str(path), backend, policy
        self.registry_path, self.acceptance_path = (
            registry_path,
            account_acceptance_path,
        )
        self.gas_client, self.funding, self.reverse_quote = (
            gas_client,
            funding_service,
            reverse_quote,
        )
        self.bankroll, self.minimum, self.hold = (
            dec(bankroll),
            dec(minimum_net),
            dec(hold_seconds),
        )
        if self.bankroll <= 0 or self.minimum <= 0 or not 0 < self.hold <= 3600:
            raise ValueError("DEX_ADMISSION_LIMITS_INVALID")
        self.clock, self.latest = clock, {}

    def identity(self, p):
        registry = json.loads(Path(self.registry_path).read_text())
        _, tokens, asset, quote = registry_scope(
            registry, 1, p.asset, p.quote, self.clock()
        )
        identity = tokens[asset]
        client = self.backend.clients[p.venue]
        m = market(client, p.symbol)
        if (
            quote != USDT_MAINNET
            or p.quote_decimals != 6
            or asset != p.asset
            or (
                identity.get("cex_venue"),
                identity.get("cex_symbol"),
                identity.get("base"),
            )
            != (p.venue, p.symbol, m["base"])
            or tokens[asset]["decimals"] != p.asset_decimals
            or tokens[quote]["decimals"] != p.quote_decimals
            or dec(m["contractSize"]) != dec(p.contract_size)
        ):
            raise ValueError("DEX_LIVE_INSTRUMENT_IDENTITY_MISMATCH")
        if not accepted(self.acceptance_path, (p.venue,), self.clock()):
            raise ValueError("DEX_CEX_ACCOUNT_ACCEPTANCE_REQUIRED")
        return client

    async def risk(self, p):
        async with aiosqlite.connect(self.path) as d:
            cur = await d.execute(
                "SELECT COALESCE(SUM(net),0),COALESCE(SUM(CASE WHEN ts>=? THEN net ELSE 0 END),0) FROM live_results",
                (int(self.clock() // 86400) * 86400,),
            )
            total, daily = await cur.fetchone()
        equity = self.bankroll + dec(total)
        if (
            equity <= 0
            or dec(daily) <= -self.bankroll * dec("0.02")
            or dec(p.budget) > min(dec(5), equity * dec("0.1"))
        ):
            raise ValueError("DEX_REALIZED_EQUITY_OR_DAILY_LOSS_LIMIT")

    async def gas(self, raw):
        m = self.gas_client.market("ETH/USDT")
        if (
            m.get("spot") is not True
            or (m.get("base"), m.get("quote")) != ("ETH", "USDT")
            or m.get("active") is not True
        ):
            raise ValueError("DEX_GAS_MARKET_IDENTITY")
        started = self.clock()
        b = normalize(
            await asyncio.wait_for(
                self.gas_client.fetch_order_book("ETH/USDT", limit=20), 8
            ),
            "ETH/USDT",
            started,
            self.clock(),
            1.5,
        )
        with localcontext() as ctx:
            ctx.prec = 80
            qty = dec(raw) / 10**18
            if qty <= 0:
                raise ValueError("DEX_GAS_BOUND_INVALID")
            avg, _ = executable(b["asks"], float(qty))
            value = qty * dec(avg)
        return value, b

    async def edge(self, p, signed_asset_raw, quote_raw, gas_raw, request):
        client = self.identity(p)
        started = self.clock()
        fee = await asyncio.wait_for(client.fetch_trading_fee(p.symbol), 8)
        if fee.get("symbol") != p.symbol:
            raise ValueError("DEX_ACCOUNT_FEE_SCOPE")
        taker = rate(fee.get("taker"))
        f = await self.funding.get(p.venue, p.symbol)
        if (
            getattr(f, "exchange", None) != p.venue
            or getattr(f, "symbol", None) != p.symbol
            or getattr(f, "next_ts", None) is None
            or getattr(f, "interval_hours", None) is None
        ):
            raise ValueError("DEX_FUNDING_CALENDAR_UNKNOWN")
        funding_rate, hours = dec(f.rate), dec(f.interval_hours)
        next_ts = dec(f.next_ts) / 1000
        if (
            hours <= 0
            or hours > 24
            or next_ts <= dec(self.clock())
            or next_ts - dec(self.clock()) > hours * 3600
        ):
            raise ValueError("DEX_FUNDING_CALENDAR_INVALID")
        gas, book = await self.gas(gas_raw)
        with localcontext() as ctx:
            ctx.prec = 80
            base = abs(dec(signed_asset_raw)) / 10**p.asset_decimals
            notional = base * dec(request.price)
            if (
                dec(request.qty) * dec(p.contract_size) != base
                or request.reduce_only is not False
                or request.side != ("sell" if p.direction == "forward" else "buy")
            ):
                raise ValueError("DEX_HEDGE_REQUEST_SCOPE")
            cash = abs(dec(quote_raw)) / 10**p.quote_decimals
            if max(cash, notional) > dec(p.budget):
                raise ValueError("DEX_POST_FILL_NOTIONAL_LIMIT")
            periods = math.ceil(float(self.hold / (hours * 3600))) + 1
            funding_reserve = abs(funding_rate) * notional * periods
            ceiling = (
                (notional - cash if p.direction == "forward" else cash - notional)
                - 2 * notional * taker
                - 2 * gas
                - funding_reserve
                - dec(p.safety)
            )
        evidence = request.market_evidence
        if (
            not 0 <= self.clock() - started <= 15
            or not 0 <= self.clock() - evidence["book_ts"] <= 1.5
            or not 0 <= self.clock() - book["timestamp"] / 1000 <= 1.5
            or ceiling < self.minimum
        ):
            raise ValueError("DEX_POST_COST_CEILING_OR_FRESHNESS_LIMIT")
        self.latest = dict(
            mode="FULL_CONVERGENCE_CEILING_NOT_REALIZED",
            ceiling=str(ceiling),
            funding_reserve=str(funding_reserve),
            taker=str(taker),
            gas_bound=str(gas),
            gas_book=book,
            cex_evidence=evidence,
            ts=self.clock(),
        )
        return True

    async def account(self, p):
        client = self.identity(p)
        await self.risk(p)
        if (
            client.has.get("fetchPositionMode") is not True
            or client.has.get("fetchTradingFee") is not True
        ):
            raise ValueError("DEX_ACCOUNT_CAPABILITIES_UNKNOWN")
        started = self.clock()
        positions, orders, mode, balance = await asyncio.gather(
            PrivateReader(p.venue, client).positions(),
            PrivateReader(p.venue, client).orders(),
            asyncio.wait_for(client.fetch_position_mode(p.symbol), 8),
            asyncio.wait_for(client.fetch_balance(), 8),
        )
        if positions or orders or mode.get("hedged") is not False:
            raise ValueError("DEX_ISOLATED_ONE_WAY_FLAT_ACCOUNT_REQUIRED")
        currency = balance.get("USDT", {})
        amounts = [
            dec(currency.get(k, balance.get(k, {}).get("USDT")))
            for k in ("free", "used", "total")
        ]
        if (
            any(x < 0 for x in amounts)
            or amounts[0] < 2 * dec(p.budget)
            or abs(amounts[0] + amounts[1] - amounts[2]) > amounts[2] * dec("1e-8")
            or not 0 <= self.clock() - started <= 15
        ):
            raise ValueError("DEX_PRIVATE_MARGIN_OR_BALANCE_UNVERIFIED")
        return True

    def check_reverse(self, p, asset, reverse):
        self.policy.check(reverse, self.clock())
        sell, buy = (p.asset, p.quote) if asset > 0 else (p.quote, p.asset)
        if (
            (reverse.proof["sell_token"], reverse.proof["buy_token"]) != (sell, buy)
            or int(reverse.proof["requested_amount_raw"]) != abs(asset)
            or reverse.proof["quote_mode"] != ("exact_in" if asset > 0 else "exact_out")
        ):
            raise ValueError("DEX_ADMISSION_REVERSE_ROUTE_MISMATCH")

    async def __call__(self, p, envelope):
        self.policy.check(envelope, self.clock())
        await self.account(p)
        q = envelope.proof
        asset = (
            int(q["buy_amount_raw"])
            if p.direction == "forward"
            else -int(q["sell_amount_raw"])
        )
        quote = (
            -int(q["max_sell_amount_raw"])
            if p.direction == "forward"
            else int(q["min_buy_amount_raw"])
        )
        # Preflight the opposite route against already-owned isolated inventory.
        reverse = await self.reverse_quote(p, asset)
        self.check_reverse(p, asset, reverse)
        request = await self.backend.prepare(p, -asset, closing=False)
        gas_raw = max(int(q["network_fee_raw"]), int(reverse.proof["network_fee_raw"]))
        return await self.edge(p, asset, quote, gas_raw, request)

    async def hedge(self, p, observation, request):
        await self.account(p)
        flow = observation["wallet"]
        # A second fresh reverse simulation bounds the unwind after the actual swap.
        reverse = await self.reverse_quote(p, flow["asset_raw"])
        self.check_reverse(p, flow["asset_raw"], reverse)
        gas_raw = max(flow["gas_raw"], int(reverse.proof["network_fee_raw"]))
        return await self.edge(
            p, flow["asset_raw"], flow["quote_raw"], gas_raw, request
        )
