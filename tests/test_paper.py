import asyncio
from app.paper import PaperEngine

class FakeDiary:
    async def open_paper_positions(self): return []
    async def create_paper_position(self, p): return 1
    async def update_paper_position(self, p): pass
    async def close_paper_position(self, p, reason): pass

def test_paper_convergence():
    async def run():
        e = PaperEngine(FakeDiary(), capital=50)
        o = dict(symbol="ABC/USDT:USDT", buy="binance", sell="bybit",
                 notional=5, entry_buy=1.0, entry_sell=1.1, executable=10.0)
        p = await e.open(o)
        mark = dict(o, exit_buy=1.025, exit_sell=1.075,
                    exit_spread=4.878, fee_pct=0.21)
        await e.mark([mark])
        assert p.current_net_usd > 0
    asyncio.run(run())
