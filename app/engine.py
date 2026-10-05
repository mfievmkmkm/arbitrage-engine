"""Public-data discovery. No order placement or simulated guaranteed fills."""
import asyncio
import time
from dataclasses import dataclass
import ccxt.async_support as ccxt

# Conservative illustrative taker fee assumptions. Override with verified account fees
# before any real trading. Exit assumed to cost another two taker fees.
FEE_BPS = {"binance": 5.0, "bybit": 5.5, "okx": 5.0,
           "bitget": 6.0, "gateio": 7.5, "mexc": 6.0, "bingx": 6.0}
ALLOWED = set(FEE_BPS)

@dataclass
class Quote:
    exchange: str
    symbol: str
    bids: list
    asks: list
    fetched: float

def vwap(levels, qty):
    remaining, total = qty, 0.0
    for price, amount in levels:
        take = min(remaining, amount)
        total += take * price
        remaining -= take
        if remaining <= 1e-10:
            return total / qty
    return None

def evaluate(buy: Quote, sell: Quote, notional: float, max_age: float,
             now=None):
    now = time.time() if now is None else now
    if buy.symbol != sell.symbol or buy.exchange == sell.exchange:
        return None
    if any(now - q.fetched > max_age or q.fetched > now + 2 for q in (buy, sell)):
        return None
    if not buy.asks or not sell.bids or buy.asks[0][0] <= 0:
        return None
    qty = notional / buy.asks[0][0]
    entry_buy = vwap(buy.asks, qty)
    entry_sell = vwap(sell.bids, qty)
    if entry_buy is None or entry_sell is None:
        return None
    raw = (sell.bids[0][0] - buy.asks[0][0]) / buy.asks[0][0] * 100
    executable = (entry_sell - entry_buy) / entry_buy * 100
    # 4 taker transactions (entry and hypothetical exit), using conservative
    # fee assumptions; funding and price movement remain UNKNOWN.
    fee = 2 * (FEE_BPS[buy.exchange] + FEE_BPS[sell.exchange]) / 100
    return dict(symbol=buy.symbol, buy=buy.exchange, sell=sell.exchange,
                raw=raw, executable=executable, fee_pct=fee,
                hypothetical_edge=executable-fee,
                notional=notional, ts=now, age_buy=now-buy.fetched,
                age_sell=now-sell.fetched)

class Scanner:
    def __init__(self, exchanges, notional, max_age):
        self.ids = [x for x in exchanges if x in ALLOWED]
        self.notional, self.max_age = notional, max_age
        self.clients = {}
        self.symbols = {}
        self.errors = {}
        self.paused = False
        self.last_scan = None

    async def start(self):
        async def init(name):
            client = getattr(ccxt, name)({"enableRateLimit": True,
                                         "options": {"defaultType": "swap"}})
            try:
                markets = await client.load_markets()
                # Linear USDT perpetuals only. Symbol equality is a preliminary
                # match, not token-contract identity verification.
                symbols = {m["symbol"] for m in markets.values()
                           if m.get("swap") and m.get("linear")
                           and m.get("settle") == "USDT" and m.get("active") is not False}
                self.clients[name], self.symbols[name] = client, symbols
            except Exception as exc:
                self.errors[name] = type(exc).__name__
                await client.close()
        await asyncio.gather(*(init(name) for name in self.ids))

    async def close(self):
        await asyncio.gather(*(c.close() for c in self.clients.values()),
                             return_exceptions=True)

    async def scan(self):
        if self.paused:
            return []
        # Limit universe to markets on >=2 venues; bounded subset to respect
        # REST rate limits. A WebSocket fan-out is required for broad coverage.
        counts = {}
        for symbols in self.symbols.values():
            for symbol in symbols:
                counts[symbol] = counts.get(symbol, 0) + 1
        common = sorted(s for s, n in counts.items() if n >= 2)[:30]
        sem = asyncio.Semaphore(5)
        async def fetch(name, symbol):
            async with sem:
                try:
                    book = await asyncio.wait_for(
                        self.clients[name].fetch_order_book(symbol, limit=20),
                        timeout=8)
                    # fetch time is used, not exchange timestamps that vary by venue.
                    return Quote(name, symbol, book["bids"], book["asks"], time.time())
                except Exception as exc:
                    self.errors[name] = type(exc).__name__
                    return None
        tasks = [fetch(name, symbol) for symbol in common
                 for name in self.clients if symbol in self.symbols[name]]
        results = await asyncio.gather(*tasks)
        grouped = {}
        for quote in results:
            if quote:
                grouped.setdefault(quote.symbol, []).append(quote)
        now = time.time()
        opportunities = []
        for quotes in grouped.values():
            for a in quotes:
                for b in quotes:
                    result = evaluate(a, b, self.notional, self.max_age, now)
                    if result and result["hypothetical_edge"] > 0:
                        opportunities.append(result)
        self.last_scan = now
        return sorted(opportunities, key=lambda x: x["hypothetical_edge"],
                      reverse=True)[:30]
