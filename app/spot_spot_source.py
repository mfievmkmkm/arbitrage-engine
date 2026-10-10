import asyncio, time, math
from .spot_spot_directional import best
from .spot_future_vwap import vwap
from .public_books import normalize


class Source:
    def __init__(self, clients, notional, fees_pct=0.2, safety_pct=0.1):
        self.clients = clients
        self.notional = notional
        self.fees_pct = fees_pct
        self.safety_pct = safety_pct
        self.allowed_venue = lambda v: True
        self.watch_routes = []

    async def scan_symbol(self, symbol):
        books = {}
        stamps = {}
        watch = [p for p in self.watch_routes if p["symbol"] == symbol]
        needed = {v for p in watch for v in (p["buy"], p["sell"])}

        async def get(v, c):
            try:
                started = time.time()
                book = await asyncio.wait_for(c.fetch_order_book(symbol), 8)
                book = normalize(
                    book,
                    symbol,
                    started,
                    time.time(),
                    12,
                    book.get("data_source", "REST"),
                )
                stamp = (
                    float(book["timestamp"]) / 1000
                    if book.get("timestamp") is not None
                    else started
                )
                if not math.isfinite(stamp) or not 0 <= time.time() - stamp <= 12:
                    return
                if not book.get("asks") or not book.get("bids"):
                    return
                books[v] = book
                stamps[v] = stamp
            except Exception:
                pass

        await asyncio.gather(
            *(
                get(v, c)
                for v, c in self.clients.items()
                if self.allowed_venue(v) or v in needed
            )
        )

        def quote(a, b, qty, watch_only=False):
            prices = [
                vwap(books[a]["asks"], qty),
                vwap(books[b]["bids"], qty),
                vwap(books[a]["bids"], qty),
                vwap(books[b]["asks"], qty),
            ]
            if any(p is None or not math.isfinite(p) or p <= 0 for p in prices):
                return None
            buy, sell, exit_buy, exit_sell = prices
            gross = (sell - buy) / buy * 100
            return dict(
                strategy="spot_spot",
                symbol=symbol,
                buy=a,
                sell=b,
                executable=gross,
                net=gross - self.fees_pct - self.safety_pct,
                hypothetical_edge=gross - self.fees_pct - self.safety_pct,
                notional=qty * buy,
                base_qty=qty,
                entry_buy=buy,
                entry_sell=sell,
                exit_buy=exit_buy,
                exit_sell=exit_sell,
                fee_pct=self.fees_pct,
                safety_pct=self.safety_pct,
                ts=min(stamps[a], stamps[b]),
                watch_only=watch_only,
                evidence_mode="PAPER_MODEL",
                book_sources={a: books[a]["data_source"], b: books[b]["data_source"]},
            )

        out = []
        names = sorted(v for v in books if self.allowed_venue(v))
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                x = best(
                    a,
                    books[a],
                    b,
                    books[b],
                    self.notional,
                    self.fees_pct,
                    self.safety_pct,
                )
                if x:
                    row = quote(x["buy_venue"], x["sell_venue"], x["qty"])
                    if row:
                        out.append(row)
        for p in watch:
            if p["buy"] in books and p["sell"] in books:
                row = quote(p["buy"], p["sell"], p["base_qty"], True)
                if row:
                    out.append(row)
        return sorted(out, key=lambda x: x["net"], reverse=True)
