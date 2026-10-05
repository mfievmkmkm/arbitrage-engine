import time
from dataclasses import dataclass, asdict

@dataclass
class PaperPosition:
    id: int
    symbol: str
    buy: str
    sell: str
    notional: float
    entry_buy: float
    entry_sell: float
    entry_spread: float
    opened_at: float
    best_net_usd: float = 0.0
    current_net_usd: float = 0.0
    current_spread: float = 0.0
    status: str = "OPEN"

class PaperEngine:
    """Conservative paper positions driven only by observed executable quotes."""
    def __init__(self, diary, capital=50.0, max_positions=2):
        self.diary = diary
        self.capital = capital
        self.max_positions = max_positions
        self.positions = {}

    async def restore(self):
        for row in await self.diary.open_paper_positions():
            p = PaperPosition(**row)
            self.positions[p.id] = p

    @property
    def used_capital(self):
        return sum(p.notional * 2 for p in self.positions.values() if p.status == "OPEN")

    def can_open(self, o):
        return (len(self.positions) < self.max_positions and
                self.used_capital + o["notional"] * 2 <= self.capital)

    async def open(self, o):
        if not self.can_open(o):
            return None
        # Scanner stores executable entry VWAPs. Paper fill equals that observed
        # VWAP; this is a model assumption, not proof that a live order would fill.
        p = PaperPosition(
            id=0, symbol=o["symbol"], buy=o["buy"], sell=o["sell"],
            notional=o["notional"], entry_buy=o["entry_buy"],
            entry_sell=o["entry_sell"], entry_spread=o["executable"],
            opened_at=time.time(), current_spread=o["executable"])
        p.id = await self.diary.create_paper_position(asdict(p))
        self.positions[p.id] = p
        return p

    async def mark(self, opportunities):
        lookup = {(o["symbol"], o["buy"], o["sell"]): o for o in opportunities}
        for p in list(self.positions.values()):
            o = lookup.get((p.symbol, p.buy, p.sell))
            if not o or not o.get("exit_buy") or not o.get("exit_sell"):
                continue
            qty = p.notional / p.entry_buy
            gross = (o["exit_buy"] - p.entry_buy) * qty + (p.entry_sell - o["exit_sell"]) * qty
            # Entry+exit fees are already represented by fee_pct estimate.
            fees = p.notional * o["fee_pct"] / 100
            net = gross - fees
            p.current_net_usd = net
            p.best_net_usd = max(p.best_net_usd, net)
            p.current_spread = o["exit_spread"]
            await self.diary.update_paper_position(asdict(p))

    async def close(self, position_id, reason="MANUAL"):
        p = self.positions.get(position_id)
        if not p:
            return None
        p.status = "CLOSED"
        await self.diary.close_paper_position(asdict(p), reason)
        self.positions.pop(position_id, None)
        return p
