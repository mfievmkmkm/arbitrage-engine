"""Public market-data discovery. No order placement."""

import asyncio, time, math, logging
from collections import Counter
from dataclasses import dataclass
from .exchange_names import public_exchange_class as exchange_class
from .public_books import wrap, normalize
from .discovery import RotatingUniverse
from .health import VenueHealth
from .instruments import from_market, min_notional_ok
from .net_edge import calculate as net_edge
from .funding_service import FundingService
from .instruments import compatible
from .contract_book import to_base_levels
from .spot_future_vwap import vwap
from .native_order_plan import prepare_pair
from .book_priority_runtime import publish as book_priorities

FEE_BPS = {
    "binance": 5.0,
    "bybit": 5.5,
    "okx": 5.0,
    "bitget": 6.0,
    "gateio": 7.5,
    "mexc": 8.0,
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
    received_at: float | None = None
    data_source: str = "REST"


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
        decision_at=now,
        base_qty=qty,
        entry_buy=eb,
        entry_sell=es,
        exit_buy=xb,
        exit_sell=xs,
        exit_spread=exit_spread,
        age_buy=now - buy.fetched,
        age_sell=now - sell.fetched,
        book_sources={buy.exchange: buy.data_source, sell.exchange: sell.data_source},
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
        self.on_books = None
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
        self.start_errors = {}
        self.health = VenueHealth()
        self.paused = False
        self.last_scan = None
        self.universe = None
        self.last_diagnostics = None

    async def start(self):
        async def init(name):
            c = exchange_class(name)(
                {"enableRateLimit": True, "options": {"defaultType": "swap"}}
            )
            try:
                ms = await c.load_markets()
                valid = [
                    m
                    for m in ms.values()
                    if m.get("swap")
                    and m.get("linear")
                    and m.get("settle") == "USDT"
                    and m.get("active") is not False
                ]
                checked = []
                for m in valid:
                    try:
                        if (
                            m.get("contractSize") is not None
                            and math.isfinite(float(m["contractSize"]))
                            and float(m["contractSize"]) > 0
                        ):
                            checked.append(m)
                    except (ValueError, TypeError):
                        continue
                valid = checked
                self.symbols[name] = {m["symbol"] for m in valid}
                self.specs[name] = {m["symbol"]: from_market(name, m) for m in valid}
                self.clients[name] = wrap(c)
                self.start_errors.pop(name, None)
            except Exception as e:
                self.errors[name] = type(e).__name__
                self.start_errors[name] = type(e).__name__
                await c.close()

        # CCXT builds large market maps. Loading every venue concurrently can
        # exceed a small Railway worker's memory limit before polling starts.
        for name in self.ids:
            logging.getLogger('arbitrage').info('Futures market bootstrap: %s start', name)
            await init(name)
            logging.getLogger('arbitrage').info(
                'Futures market bootstrap: %s %s', name,
                'ready' if name in self.clients else 'unavailable:' + self.start_errors.get(name, 'unknown'))
        # MEXC contract depth is capped at 10 requests / 2 s. Its shared
        # 0.25 s pacer needs a bounded batch within the quote freshness window.
        batch = min(self.batch_size, 16) if 'mexc' in self.clients else self.batch_size
        self.universe = RotatingUniverse(self.symbols, self.universe_size, batch)
        self.funding = FundingService(self.clients)

    async def close(self):
        await asyncio.gather(
            *(c.close() for c in self.clients.values()), return_exceptions=True
        )

    async def scan(self):
        started_at = time.time()
        counters = Counter()
        venue_books = {v: Counter() for v in self.clients}
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
        required = {
            v: [
                s
                for s, a, b in sorted(self.watch_routes)
                if v in (a, b) and s in self.symbols[v]
            ]
            for v in self.clients
        }
        candidates = {
            v: (
                [s for s in symbols if s in self.symbols[v]]
                if not self.paused and v in getattr(self, "scan_enabled", self.clients)
                else []
            )
            for v in self.clients
        }
        book_priorities(self.clients, "futures_scanner", required, candidates)
        # A venue waiting for its own rate window must not occupy another
        # venue's book slots and make the two-sided quote stale.
        slots = {name: asyncio.Semaphore(self.concurrency) for name in self.clients}

        async def fetch(name, symbol):
            async with slots[name]:
                counters['books_requested'] += 1
                venue_books[name]['requested'] += 1
                started = time.perf_counter()
                requested_at = time.time()
                try:
                    b = await asyncio.wait_for(
                        self.clients[name].fetch_order_book(symbol, limit=20), timeout=8
                    )
                    received_at = b.get("received_at", time.time())
                    source = b.get("data_source", "REST")
                    b = normalize(
                        b, symbol, requested_at, time.time(), self.max_age, source
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
                        counters['books_stale'] += 1
                        return None
                    self.health.success(name, (time.perf_counter() - started) * 1000)
                    spec = self.specs[name][symbol]
                    if not b.get("asks") or not b.get("bids"):
                        counters['books_empty'] += 1
                        return None
                    counters['books_ok'] += 1
                    venue_books[name]['ok'] += 1
                    return Quote(
                        name,
                        symbol,
                        to_base_levels(b["bids"], spec.contract_size),
                        to_base_levels(b["asks"], spec.contract_size),
                        stamp,
                        received_at,
                        source,
                    )
                except Exception as e:
                    counters['book_errors'] += 1
                    counters['book_error:' + type(e).__name__] += 1
                    venue_books[name]['errors'] += 1
                    if isinstance(e, ValueError) and str(e) == 'PUBLIC_BOOK_STALE':
                        counters['books_stale'] += 1
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
        if self.on_books:
            await self.on_books([q for q in rs if q], self.specs)
        now = time.time()
        ops = []
        for qs in grouped.values():
            for a in qs:
                for b in qs:
                    if a.exchange == b.exchange:
                        continue
                    counters['pairs_checked'] += 1
                    if not compatible(
                        self.specs[a.exchange][a.symbol],
                        self.specs[b.exchange][b.symbol],
                    ):
                        counters['pairs_incompatible'] += 1
                        continue
                    if not min_notional_ok(
                        self.specs[a.exchange][a.symbol], self.notional, a.asks[0][0]
                    ) or not min_notional_ok(
                        self.specs[b.exchange][b.symbol], self.notional, b.bids[0][0]
                    ):
                        counters['pairs_below_minimum'] += 1
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
                    if r is None:
                        counters['pairs_stale_or_depth'] += 1
                    elif r['hypothetical_edge'] <= 0:
                        counters['pairs_no_edge_before_funding'] += 1
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
            native = prepare_pair(
                o["symbol"],
                o["buy"],
                o["sell"],
                self.clients[o["buy"]],
                self.clients[o["sell"]],
                o["base_qty"],
                o["entry_buy"],
                o["entry_sell"],
            )
            watched_position = self.watch_positions.get(
                (o["symbol"], o["buy"], o["sell"])
            )
            if native.valid and not watched_position:
                qs = grouped[o["symbol"]]
                a = next(q for q in qs if q.exchange == o["buy"])
                b = next(q for q in qs if q.exchange == o["sell"])
                actual = evaluate(
                    a, b, self.notional, self.max_age, now, base_qty=native.base_qty
                )
                if actual:
                    o.update(actual)
                    native = prepare_pair(
                        o["symbol"],
                        o["buy"],
                        o["sell"],
                        self.clients[o["buy"]],
                        self.clients[o["sell"]],
                        o["base_qty"],
                        o["entry_buy"],
                        o["entry_sell"],
                    )
            o["native_plan"] = native.row()
            if not native.valid:
                counters['native_block:' + native.reason] += 1
            if (
                watched_position
                and native.valid
                and not math.isclose(native.base_qty, o["base_qty"], rel_tol=1e-10)
            ):
                o["native_plan"].update(valid=False, reason="SAVED_QUANTITY_NOT_NATIVE")
            o["safety_pct"] = self.safety_buffer_pct
            o["funding_known"] = False
            o["funding_status"] = "UNKNOWN"
            o["hypothetical_edge"] = net_edge(
                o["executable"], o["fee_pct"], 0, self.safety_buffer_pct
            ).net_pct
        if self.funding:
            # Bound independent pairs instead of serially waiting up to two
            # funding timeouts per candidate (up to 50 candidates per cycle).
            funding_slots = asyncio.Semaphore(max(1, self.concurrency // 2))
            async def enrich(o):
                async with funding_slots:
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
                if not known:
                    counters['funding_unknown'] += 1
            await asyncio.gather(*(enrich(o) for o in ops))
            ops.sort(key=lambda x: x["hypothetical_edge"], reverse=True)
        self.last_scan = now
        self.last_diagnostics = dict(
            ts=started_at, finished_at=time.time(),
            duration_seconds=time.time() - started_at,
            symbols_selected=len(symbols), venues_online=len(self.clients),
            venues_configured=list(self.ids), venue_start_errors=dict(self.start_errors),
            universe_total=self.universe.total_coverage if self.universe else 0,
            symbols_scanned=list(symbols),
            symbols_with_books=len(grouped), candidates_returned=len(ops),
            symbols_with_two_books=sum(len(qs) >= 2 for qs in grouped.values()),
            paused=self.paused, counters=dict(counters),
            venue_books={v: dict(counts) for v, counts in venue_books.items()},
        )
        return ops
