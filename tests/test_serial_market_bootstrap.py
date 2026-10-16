import asyncio

from app.engine import Scanner


def test_seven_futures_markets_load_without_parallel_memory_spike(monkeypatch):
    import app.engine as engine

    venues = 'binance bybit okx bitget gateio mexc bingx'.split()
    active = 0
    peak = 0
    loaded = []

    class Client:
        def __init__(self, venue):
            self.venue = venue

        async def load_markets(self):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            loaded.append(self.venue)
            return {}

        async def close(self):
            pass

    def exchange_class(venue):
        return lambda options: Client(venue)

    monkeypatch.setattr(engine, 'exchange_class', exchange_class)
    monkeypatch.setattr(engine, 'wrap', lambda client: client)
    scanner = Scanner(venues, 100, 12)
    asyncio.run(scanner.start())
    assert peak == 1
    assert loaded == venues
    assert set(scanner.clients) == set(venues)
