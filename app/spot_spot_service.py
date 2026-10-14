from .spot_spot_source import Source
from .book_priority_runtime import publish as book_priorities


class Service:
    def __init__(
        self,
        clients,
        symbols,
        notional,
        fees_pct=0.2,
        safety_pct=0.1,
        batch=5,
        paper=None,
    ):
        self.source = Source(clients, notional, fees_pct, safety_pct)
        self.symbols = list(symbols)
        self.batch = batch
        self.i = 0
        self.paper = paper
        self.entry_enabled = True

    async def cycle(self):
        xs = []
        if self.symbols and self.entry_enabled:
            xs = [
                self.symbols[(self.i + j) % len(self.symbols)]
                for j in range(min(self.batch, len(self.symbols)))
            ]
            self.i = (self.i + len(xs)) % len(self.symbols)
        self.source.watch_routes = self.paper.watch_routes() if self.paper else []
        xs = list(dict.fromkeys(xs + [p["symbol"] for p in self.source.watch_routes]))
        required = {
            v: [
                p["symbol"]
                for p in self.source.watch_routes
                if v in (p["buy"], p["sell"])
            ]
            for v in self.source.clients
        }
        candidates = {
            v: xs if self.source.allowed_venue(v) else [] for v in self.source.clients
        }
        book_priorities(self.source.clients, "spot_spot_scanner", required, candidates)
        out = []
        for s in xs:
            out.extend(await self.source.scan_symbol(s))
        if self.paper:
            await self.paper.cycle(out, self.entry_enabled)
        return sorted(
            [x for x in out if not x.get("watch_only")],
            key=lambda x: x["net"],
            reverse=True,
        )[:50]
