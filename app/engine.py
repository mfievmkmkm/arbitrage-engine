"""Public market-data discovery. No order placement."""

import asyncio, time, math
from dataclasses import dataclass
from .exchange_names import exchange_class
from .discovery import RotatingUniverse
from .health import VenueHealth
from .instruments import from_market, min_notional_ok
from .net_edge import calculate as net_edge
from .funding_service import FundingService
from .instruments import compatible
from .contract_book import to_base_levels
from .spot_future_vwap import vwap

FEE_BPS = {
    "binance": 5.0,
    "bybit": 5.5,
    "okx": 5.0,
    "bitget": 6.0,
    "gateio": 7.5,
    "mexc": 6.0,
    "bingx": 6.0,
}
ALLOWED = set(FEE_BPS)


@dataclass
class Quote:
    exchange: str
    symbol: str
    bids: list
    asks: list
    fetched: float


def evaluate(buy, sell, notional, max_age, now=None, base_qty=None):
    now = time.time() if now is None else now
    if buy.symbol != sell.symbol or buy.exchange == sell.exchange:
        return None
    if any(now - q.fetched > max_age or q.fetched > now + 2 for q in (buy, sell)):
        return None
    if not buy.asks or not buy.bids or not sell.bids or not sell.asks:
        return None
    qty = notional / buy.asks[0][0] if base_qty is None else base_qty
    eb = vwap(buy.asks, qty)
    es = vwap(sell.bids, qty)
    xb = vwap(buy.bids, qty)
    xs = vwap(sell.asks, qty)
    if None in (eb, es, xb, xs):
        return None
    raw = (sell.bids[0][0] - buy.asks[0][0]) / buy.asks[0][0] * 100
    exe = (es - eb) / eb * 100
    exit_spread = (xs - xb) / xb * 100
    fee = 2 * (FEE_BPS[buy.exchange] + FEE_BPS[sell.exchange]) / 100
    edge = net_edge(exe, fee)
    return dict(
        symbol=buy.symbol,
        buy=buy.exchange,
        sell=sell.exchange,
        raw=raw,
        executable=exe,
        fee_pct=fee,
        funding_pct=0.0,
        safety_pct=0.0,
        hypothetical_edge=edge.net_pct,
        notional=notional,
        ts=min(buy.fetched, sell.fetched),
        base_qty=qty,
        entry_buy=eb,
        entry_sell=es,
        exit_buy=xb,
        exit_sell=xs,
        exit_spread=exit_spread,
        age_buy=now - buy.fetched,
        age_sell=now - sell.fetched,
    )


class Scanner:
    def __init__(
        self,
        exchanges,
        notional,
        max_age,
        universe_size=120,
        batch_size=30,
        concurrency=8,
        safety_buffer_pct=0.10,
        hold_seconds=1200,
    ):
        self.watch_routes = set()
        self.watch_positions = {}
        self.ids = [x for x in exchanges if x in ALLOWED]
        self.notional = notional
        self.max_age = max_age
        self.universe_size = universe_size
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.safety_buffer_pct = safety_buffer_pct
        self.hold_seconds = hold_seconds
        self.clients = {}
        self.symbols = {}
        self.specs = {}
        self.funding = None
        self.errors = {}
        self.health = VenueHealth()
        self.paused = False
        self.last_scan = None
        self.universe = None

    async def start(self):
        async def init(name):
            c = exchange_class(name)(
                {"enableRateLimit": True, "options": {"defaultType": "swap"}}
            )
            try:
                ms = await c.load_markets()
                self.clients[name] = c
                valid = [
                    m
                    for m in ms.values()
                    if m.get("swap")
                    and m.get("linear")
                    and m.get("settle") == "USDT"
                    and m.get("active") is not False
                ]
                self.symbols[name] = {m["symbol"] for m in valid}
                self.specs[name] = {m["symbol"]: from_market(name, m) for m in valid}
            except Exception as e:
                self.errors[name] = type(e).__name__
                await c.close()

        await asyncio.gather(*(init(x) for x in self.ids))
        self.universe = RotatingUniverse(
            self.symbols, self.universe_size, self.batch_size
        )
        self.funding = FundingService(self.clients)

    async def close(self):
        await asyncio.gather(
            *(c.close() for c in self.clients.values()), return_exceptions=True
        )

    async def scan(self):
        symbols = list(
            dict.fromkeys(
                [x[0] for x in sorted(self.watch_routes)]
                + (
                    self.universe.next_batch()
                    if self.universe and not self.paused
                    else []
                )
            )
        )
        sem = asyncio.Semaphore(self.concurrency)

        async def fetch(name, symbol):
            async with sem:
                started = time.perf_counter()
                requested_at = time.time()
                try:
                    b = await asyncio.wait_for(
                        self.clients[name].fetch_order_book(symbol, limit=20), timeout=8
                    )
                    stamp = (
                        float(b["timestamp"]) / 1000
                        if b.get("timestamp") is not None
                        else requested_at
                    )
                    if (
                        not math.isfinite(stamp)
                        or not 0 <= time.time() - stamp <= self.max_age
                    ):
                        return None
                    self.health.success(name, (time.perf_counter() - started) * 1000)
                    spec = self.specs[name][symbol]
                    if not b.get("asks") or not b.get("bids"):
                        return None
                    return Quote(
                        name,
                        symbol,
                        to_base_levels(b["bids"], spec.contract_size),
                        to_base_levels(b["asks"], spec.contract_size),
                        stamp,
                    )
                except Exception as e:
                    self.errors[name] = type(e).__name__
                    self.health.failure(name, type(e).__name__)
                    return None

        rs = await asyncio.gather(
            *(
                fetch(n, s)
                for s in symbols
                for n in self.clients
                if s in self.symbols[n]
                and (
                    (
                        not self.paused
                        and n in getattr(self, "scan_enabled", self.clients)
                    )
                    or any(
                        route[0] == s and n in route[1:] for route in self.watch_routes
                    )
                )
            )
        )
        grouped = {}
        for q in rs:
            if q:
                grouped.setdefault(q.symbol, []).append(q)
        now = time.time()
        ops = []
        for qs in grouped.values():
            for a in qs:
                for b in qs:
                    if not compatible(
                        self.specs[a.exchange][a.symbol],
                        self.specs[b.exchange][b.symbol],
                    ):
                        continue
                    if not min_notional_ok(
                        self.specs[a.exchange][a.symbol], self.notional, a.asks[0][0]
                    ) or not min_notional_ok(
                        self.specs[b.exchange][b.symbol], self.notional, b.bids[0][0]
                    ):
                        continue
                    position = self.watch_positions.get(
                        (a.symbol, a.exchange, b.exchange)
                    )
                    qty = (
                        (position.base_qty or position.notional / position.entry_buy)
                        if position
                        else None
                    )
                    r = evaluate(a, b, self.notional, self.max_age, now, base_qty=qty)
                    if r and (
                        r["hypothetical_edge"] > 0
                        or (r["symbol"], r["buy"], r["sell"]) in self.watch_routes
                    ):
                        ops.append(r)
        watched = [
            x for x in ops if (x["symbol"], x["buy"], x["sell"]) in self.watch_routes
        ]
        ranked = [
            x
            for x in ops
            if (x["symbol"], x["buy"], x["sell"]) not in self.watch_routes
        ]
        if self.paused:
            ranked = []
        ops = (
            watched
            + sorted(ranked, key=lambda x: x["hypothetical_edge"], reverse=True)[:50]
        )
        for o in ops:
            o["safety_pct"] = self.safety_buffer_pct
            o["funding_known"] = False
            o["funding_status"] = "UNKNOWN"
            o["hypothetical_edge"] = net_edge(
                o["executable"], o["fee_pct"], 0, self.safety_buffer_pct
            ).net_pct
        if self.funding:
            for o in ops:
                carry, known, status = await self.funding.pair_carry_pct(
                    o["buy"], o["sell"], o["symbol"], self.hold_seconds
                )
                o["funding_pct"] = carry
                o["funding_known"] = known
                o["funding_status"] = status
                o["safety_pct"] = self.safety_buffer_pct
                o["hypothetical_edge"] = net_edge(
                    o["executable"], o["fee_pct"], carry, self.safety_buffer_pct
                ).net_pct
            ops.sort(key=lambda x: x["hypothetical_edge"], reverse=True)
        self.last_scan = now
        return ops
