"""Bounded public REST/WS snapshots, in base units, for offline research."""

import json
import math
import time
from dataclasses import asdict
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_books(id INTEGER PRIMARY KEY,venue TEXT,symbol TEXT,book_ts REAL,received_at REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS idx_market_books_route_time ON market_books(symbol,venue,received_at);
CREATE TABLE IF NOT EXISTS execution_replay_runs(id INTEGER PRIMARY KEY,created_at REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS execution_replay_results(id INTEGER PRIMARY KEY,run_id INTEGER,position_id INTEGER,scenario TEXT,status TEXT,net REAL,payload TEXT);
"""


def valid_book(book):
    try:
        if book["mode"] not in ("PUBLIC_REST_BASE_UNITS", "PUBLIC_WS_BASE_UNITS"):
            return False
        if (
            not all(math.isfinite(float(book[k])) for k in ("book_ts", "received_at"))
            or book["book_ts"] > book["received_at"]
        ):
            return False
        spec = book["instrument"]
        if (
            not spec["contract"]
            or not spec["linear"]
            or spec["settle"] != "USDT"
            or spec["quote"] != "USDT"
            or not spec["base"]
        ):
            return False
        if not math.isfinite(spec["contract_size"]) or spec["contract_size"] <= 0:
            return False
        for key in ("bids", "asks"):
            levels = book[key]
            if not levels or len(levels) > 20:
                return False
            if any(
                len(row) != 2
                or not all(math.isfinite(float(x)) for x in row)
                or row[0] <= 0
                or row[1] < 0
                for row in levels
            ):
                return False
            prices = [row[0] for row in levels]
            if prices != sorted(prices, reverse=key == "bids"):
                return False
        return book["bids"][0][0] <= book["asks"][0][0]
    except (KeyError, TypeError, ValueError):
        return False


class Store:
    def __init__(
        self, path, max_rows=200000, retention_seconds=259200, clock=time.time
    ):
        if (
            max_rows <= 0
            or not math.isfinite(retention_seconds)
            or retention_seconds <= 0
        ):
            raise ValueError("BOOK_HISTORY_CONFIG_INVALID")
        self.path = str(path)
        self.max_rows = max_rows
        self.retention = retention_seconds
        self.clock = clock

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.commit()

    async def record(self, quotes, specs):
        rows = []
        for q in quotes:
            received = q.received_at if q.received_at is not None else self.clock()
            book = dict(
                mode="PUBLIC_WS_BASE_UNITS" if getattr(q, "data_source", "REST") == "WS" else "PUBLIC_REST_BASE_UNITS",
                venue=q.exchange,
                symbol=q.symbol,
                book_ts=q.fetched,
                received_at=received,
                bids=q.bids[:20],
                asks=q.asks[:20],
                instrument=asdict(specs[q.exchange][q.symbol]),
            )
            if valid_book(book):
                rows.append(
                    (q.exchange, q.symbol, q.fetched, received, json.dumps(book))
                )
        async with aiosqlite.connect(self.path) as d:
            await d.executemany(
                "INSERT INTO market_books(venue,symbol,book_ts,received_at,payload) VALUES(?,?,?,?,?)",
                rows,
            )
            await d.execute(
                "DELETE FROM market_books WHERE received_at<?",
                (self.clock() - self.retention,),
            )
            await d.execute(
                "DELETE FROM market_books WHERE id IN (SELECT id FROM market_books ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (self.max_rows,),
            )
            await d.commit()

    async def stats(self):
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT COUNT(*),MIN(received_at),MAX(received_at) FROM market_books"
            ) as c:
                count, first, last = await c.fetchone()
        return dict(count=count, first=first, last=last)
