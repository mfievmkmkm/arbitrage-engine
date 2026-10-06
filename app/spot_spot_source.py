import asyncio
from .spot_spot_directional import best


class Source:
    def __init__(self, clients, notional, fees_pct=0.2, safety_pct=0.1):
        self.clients = clients
        self.notional = notional
        self.fees_pct = fees_pct
        self.safety_pct = safety_pct
        self.allowed_venue = lambda v: True

    async def scan_symbol(self, symbol):
        books = {}

        async def get(v, c):
            try:
                books[v] = await asyncio.wait_for(c.fetch_order_book(symbol), 8)
            except Exception:
                pass

        await asyncio.gather(
            *(get(v, c) for v, c in self.clients.items() if self.allowed_venue(v))
        )
        out = []
        names = sorted(books)
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
                    out.append(
                        {
                            "strategy": "spot_spot",
                            "symbol": symbol,
                            "buy": x["buy_venue"],
                            "sell": x["sell_venue"],
                            "executable": x["gross"],
                            "net": x["net"],
                            "hypothetical_edge": x["net"],
                            "notional": self.notional,
                            "base_qty": x["qty"],
                            "entry_buy": x["buy"],
                            "entry_sell": x["sell"],
                        }
                    )
        return sorted(out, key=lambda x: x["net"], reverse=True)
