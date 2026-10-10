"""Read-only final costs: private mature funding and explicit ETH gas valuation.

USDT token units are not USD. Native gas is actually paid in ETH; its USDT
replacement cost is valued at a fresh public ask, never called a filled trade.
"""

import asyncio
import time
from decimal import localcontext
from .dex_live_bridge import dec
from .private_funding_reader import Reader as FundingReader
from .public_books import normalize
from .recovery_market import executable

USDT_MAINNET = "0xdac17f958d2ee523a2206206994597c13d831ec7"


class Reader:
    def __init__(self, futures_clients, gas_client, clock=time.time):
        self.futures_clients, self.gas_client, self.clock = (
            futures_clients,
            gas_client,
            clock,
        )

    async def __call__(self, plan, observation, closed_at):
        if plan.quote != USDT_MAINNET or plan.quote_decimals != 6:
            raise ValueError("DEX_FINAL_QUOTE_NOT_MAINNET_USDT")
        gas_raw = observation["wallet"]["gas_raw"]
        if type(gas_raw) is not int or gas_raw <= 0:
            raise ValueError("DEX_FINAL_GAS_UNVERIFIED")
        m = self.gas_client.market("ETH/USDT")
        if (
            m.get("spot") is not True
            or m.get("active") is not True
            or (m.get("base"), m.get("quote")) != ("ETH", "USDT")
        ):
            raise ValueError("DEX_GAS_INSTRUMENT_MISMATCH")
        funding = await FundingReader(
            plan.venue, self.futures_clients[plan.venue], clock=self.clock
        ).collect(plan.symbol, plan.opened_at, closed_at)
        if not funding.verified or funding.covered_until < closed_at:
            raise ValueError(funding.reason)
        started = self.clock()
        raw = await asyncio.wait_for(
            self.gas_client.fetch_order_book("ETH/USDT", limit=20), 8
        )
        book = normalize(raw, "ETH/USDT", started, self.clock(), 1.5)
        with localcontext() as ctx:
            ctx.prec = 80
            native = dec(gas_raw) / 10**18
            # Require replacement depth rather than an unexecutable top-of-book mark.
            price, worst = executable(book["asks"], float(native))
            valuation = native * dec(price)
        result = dict(
            verified=True,
            trade_id=observation["trade_id"],
            venue=plan.venue,
            symbol=plan.symbol,
            gas_raw=str(gas_raw),
            hashes=observation["wallet"]["hashes"],
            gas_usdt=str(valuation),
            funding=str(funding.amount),
            funding_covered_until=funding.covered_until,
            funding_events=list(funding.events),
            funding_verified_at=self.clock(),
            quote_usdt_identity_evidence=dict(
                chain_id=1, token=USDT_MAINNET, decimals=6
            ),
            gas_valuation_evidence=dict(
                method="CURRENT_EXECUTABLE_ETH_REPLACEMENT_ASK_NOT_A_FILL",
                native_asset="ETH",
                native_raw=str(gas_raw),
                symbol="ETH/USDT",
                venue=self.gas_client.id,
                book=book,
                average_price=price,
                worst_price=worst,
                market_identity=dict(spot=True, active=True, base="ETH", quote="USDT"),
            ),
        )
        from .live_cash_dex_attribution import dex_costs

        dex_costs(plan, result, observation["wallet"], closed_at)
        return result
