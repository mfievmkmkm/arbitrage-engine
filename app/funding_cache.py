import time


class FundingCache:
    def __init__(self, ttl=60):
        self.ttl = ttl
        self.data = {}

    def put(self, exchange, symbol, rate, next_ts=None, interval_hours=None, now=None):
        self.data[(exchange, symbol)] = (
            time.time() if now is None else now,
            rate,
            next_ts,
            interval_hours,
        )

    def get(self, exchange, symbol, now=None):
        row = self.data.get((exchange, symbol))
        if not row:
            return None
        ts, rate, next_ts, interval_hours = row
        now = time.time() if now is None else now
        if now < ts or now - ts > self.ttl:
            return None
        return {
            "rate": rate,
            "next_ts": next_ts,
            "interval_hours": interval_hours,
            "age": now - ts,
        }
