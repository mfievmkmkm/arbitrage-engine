class CycleService:
    def __init__(self, service, symbols, batch=5, paper=None):
        self.service = service
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
        out = []
        for s in xs:
            for x in await self.service.scan(s):
                out.append(
                    dict(
                        strategy="funding_arb",
                        symbol=s,
                        long_venue=x.long_venue,
                        short_venue=x.short_venue,
                        carry_pct=x.gross_carry_pct,
                        projected_net_pct=x.carry_pct,
                        evidence_mode="FUNDING_FORECAST",
                    )
                )
        if self.paper:
            await self.paper.cycle(out, self.entry_enabled)
        return sorted(out, key=lambda x: x["projected_net_pct"], reverse=True)
