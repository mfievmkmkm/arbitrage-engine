"""The broad spot scan must not pin raw CCXT ticker payloads in memory."""
import asyncio

from app.market_signals import Scanner


def test_bounded_ticker_cache_keeps_only_executable_prices(tmp_path):
    async def check():
        huge = "x" * 100000
        class Client:
            has = {"fetchTickers": True}
            markets = {
                "GOOD/USDT": dict(spot=True, active=True, base="GOOD", quote="USDT"),
                "BAD/USDT": dict(spot=True, active=True, base="BAD", quote="USDT"),
            }
            async def fetch_tickers(self):
                return {
                    "GOOD/USDT": dict(bid="1.01", ask="1.02", info={"huge": huge},
                                      raw_levels=[huge]),
                    "BAD/USDT": dict(bid="nan", ask="1.02", info={"huge": huge}),
                }
            async def close(self):
                pass

        scanner = Scanner(tmp_path / "audit.sqlite3", None, clock=lambda: 100,
                          venues=[], shared_clients={"example": Client()})
        await scanner.tick("example")
        _, rows = scanner.tickers["example"]
        assert rows == {"GOOD/USDT": {"bid": 1.01, "ask": 1.02}}
        assert "info" not in rows["GOOD/USDT"]
        assert "raw_levels" not in rows["GOOD/USDT"]
        await scanner.close()
    asyncio.run(check())
