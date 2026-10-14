import asyncio
from app.spot_future_source import SpotFutureSource


class C:
    async def load_markets(self):
        return {
            "s": {"symbol": "X/USDT", "base": "X", "quote": "USDT", "spot": True},
            "f": {
                "symbol": "X/USDT:USDT",
                "base": "X",
                "quote": "USDT",
                "contractSize": 1,
                "swap": True,
                "linear": True,
                "settle": "USDT",
            },
        }

    async def fetch_order_book(self, s):
        return {
            "asks": [[100 if ":" not in s else 105, 1]],
            "bids": [[99.9 if ":" not in s else 104.9, 1]],
        }


def test_source_scans_normalized_pairs():
    async def go():
        x = SpotFutureSource({"a": C()}, 10)
        await x.load()
        r = await x.scan()
        assert r and r[0]["strategy"] == "spot_futures"

    asyncio.run(go())
