"""Observe executable closing VWAP with account-specific exit fee evidence."""

import asyncio, math, time
from .engine import vwap
from .contract_book import to_base_levels


class Reader:
    def __init__(
        self, public_clients, private_clients, max_age=1.5, timeout=8, clock=time.time
    ):
        self.public = public_clients
        self.private = private_clients
        self.max_age = max_age
        self.timeout = timeout
        self.clock = clock
        self.fee_cache = {}

    async def _book(self, venue, symbol, size):
        client = self.public.get(venue)
        if client is None:
            raise ValueError("MARKET_VENUE_MISSING")
        market = client.market(symbol)
        actual = market.get("contractSize")
        if actual is None or not math.isclose(float(actual), size, rel_tol=1e-10):
            raise ValueError("CONTRACT_SIZE_MISMATCH")
        started = self.clock()
        book = await asyncio.wait_for(
            client.fetch_order_book(symbol, limit=20), self.timeout
        )
        stamp = book.get("timestamp")
        stamp = float(stamp) / 1000 if stamp is not None else started
        if not math.isfinite(stamp):
            raise ValueError("EXIT_BOOK_TIMESTAMP_INVALID")
        if self.clock() - stamp > self.max_age or stamp > self.clock() + 1:
            raise ValueError("EXIT_BOOK_STALE")
        if not book.get("bids") or not book.get("asks"):
            raise ValueError("EXIT_BOOK_EMPTY")
        return {
            "bids": to_base_levels(book["bids"], size),
            "asks": to_base_levels(book["asks"], size),
            "ts": stamp,
        }

    async def _fee(self, venue, symbol):
        key = (venue, symbol)
        cached = self.fee_cache.get(key)
        if cached and self.clock() - cached[0] < 60:
            return cached[1]
        c = self.private.get(venue)
        if c is None or c.has.get("fetchTradingFee") is not True:
            return None
        try:
            row = await asyncio.wait_for(c.fetch_trading_fee(symbol), self.timeout)
            if row.get("symbol") != symbol or row.get("taker") is None:
                return None
            rate = float(row["taker"])
            if not math.isfinite(rate) or rate < 0:
                return None
            self.fee_cache[key] = (self.clock(), rate)
            return rate
        except Exception:
            return None

    async def mark(self, trade):
        try:
            long, short = await asyncio.gather(
                self._book(trade.long_venue, trade.symbol, trade.long_contract_size),
                self._book(trade.short_venue, trade.symbol, trade.short_contract_size),
            )
            if self.clock() - min(long["ts"], short["ts"]) > self.max_age:
                raise ValueError("EXIT_BOOK_STALE")
            lp = vwap(long["bids"], trade.base_qty)
            sp = vwap(short["asks"], trade.base_qty)
            if lp is None or sp is None:
                raise ValueError("EXIT_LIQUIDITY_LOW")
            if not all(math.isfinite(p) and p > 0 for p in (lp, sp)):
                raise ValueError("EXIT_PRICE_INVALID")
            lf, sf = await asyncio.gather(
                self._fee(trade.long_venue, trade.symbol),
                self._fee(trade.short_venue, trade.symbol),
            )
            if self.clock() - min(long["ts"], short["ts"]) > self.max_age:
                raise ValueError("EXIT_BOOK_STALE_AFTER_FEE_FETCH")
            return {
                "ok": True,
                "long_exit": lp,
                "short_exit": sp,
                "exit_fee": (
                    trade.base_qty * (lp * lf + sp * sf)
                    if lf is not None and sf is not None
                    else None
                ),
                "fees_verified": lf is not None and sf is not None,
                "ts": min(long["ts"], short["ts"]),
                "spread": (sp - lp) / lp * 100,
            }
        except Exception as error:
            return {
                "ok": False,
                "reason": (
                    str(error)
                    if isinstance(error, ValueError)
                    else "EXIT_MARKET_UNAVAILABLE"
                ),
            }
