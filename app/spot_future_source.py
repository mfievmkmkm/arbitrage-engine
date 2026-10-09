import asyncio, time, math
from .contract_book import to_base_levels
from .spot_future_symbols import normalize
from .spot_future_scanner import evaluate
from .public_books import normalize as normalize_book


class SpotFutureSource:
    def __init__(self, clients, notional, fee_pct=0.2, safety_pct=0.1, concurrency=6):
        self.clients = clients
        self.notional = notional
        self.fee_pct = fee_pct
        self.safety_pct = safety_pct
        self.sem = asyncio.Semaphore(concurrency)
        self.pairs = {}
        self.markets = {}
        self.cursor = {}
        self.watch_pairs = set()
        self.watch_positions = {}
        self.allowed_venue = lambda v: True

    async def load(self):
        for venue, c in self.clients.items():
            try:
                markets = await c.load_markets()
                self.markets[venue] = {m["symbol"]: m for m in markets.values()}
                self.pairs[venue] = normalize(markets)
            except Exception:
                self.pairs[venue] = []

    async def _one(self, venue, c, pair, funding_pct=0):
        async with self.sem:
            try:

                async def fetch(symbol):
                    started = time.time()
                    book = await asyncio.wait_for(c.fetch_order_book(symbol), 8)
                    book = normalize_book(
                        book,
                        symbol,
                        started,
                        time.time(),
                        12,
                        book.get("data_source", "REST"),
                    )
                    stamp = book.get("timestamp")
                    if stamp is not None and not math.isfinite(float(stamp)):
                        raise ValueError("INVALID_BOOK_TIMESTAMP")
                    if time.time() - started > 12:
                        raise ValueError("SLOW_BOOK")
                    if stamp is not None and (
                        time.time() - float(stamp) / 1000 > 12
                        or float(stamp) / 1000 > time.time() + 2
                    ):
                        raise ValueError("STALE_BOOK")
                    return book, float(stamp) / 1000 if stamp is not None else started

                (s, st), (f, ft) = await asyncio.gather(
                    fetch(pair.spot_symbol), fetch(pair.future_symbol)
                )
                if abs(st - ft) > 12 or time.time() - min(st, ft) > 12:
                    return None
                cs = self.markets[venue][pair.future_symbol].get("contractSize")
                if cs is None or float(cs) <= 0:
                    return None
                f = {
                    **f,
                    "bids": to_base_levels(f["bids"], float(cs)),
                    "asks": to_base_levels(f["asks"], float(cs)),
                }
                position = self.watch_positions.get((venue, pair.base))
                row = evaluate(
                    venue,
                    pair.base,
                    pair.spot_symbol,
                    pair.future_symbol,
                    s,
                    f,
                    self.notional,
                    self.fee_pct,
                    funding_pct,
                    self.safety_pct,
                    now=min(st, ft),
                    base_qty=position.base_qty if position else None,
                )
                if row is not None:
                    row["book_sources"] = {
                        pair.spot_symbol: s["data_source"],
                        pair.future_symbol: f["data_source"],
                    }
                return row
            except Exception:
                return None

    async def scan(self, limit_per_venue=20):
        jobs = []
        for v, c in self.clients.items():
            pairs = self.pairs.get(v, [])
            if not pairs:
                continue
            start = self.cursor.get(v, 0)
            n = min(limit_per_venue, len(pairs))
            selected = (
                [pairs[(start + i) % len(pairs)] for i in range(n)]
                if self.allowed_venue(v)
                else []
            )
            self.cursor[v] = (start + n) % len(pairs)
            selected.extend(
                p
                for p in pairs
                if (v, p.base) in self.watch_pairs and p not in selected
            )
            jobs.extend(self._one(v, c, p) for p in selected)
        rows = await asyncio.gather(*jobs) if jobs else []
        return sorted(
            [x for x in rows if x], key=lambda x: x["hypothetical_edge"], reverse=True
        )
