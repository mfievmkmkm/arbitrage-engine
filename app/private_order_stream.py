"""Read-only private order events; REST snapshots still own account truth."""

import asyncio
import json
import math
import time
from collections import OrderedDict
import aiosqlite
from .private_order_reader import Reader


class Streams:
    def __init__(
        self,
        clients,
        path,
        on_event=None,
        clock=time.time,
        timeout=60,
        retry=2,
        capacity=4000,
    ):
        self.clients, self.path, self.on_event = clients, str(path), on_event
        self.clock, self.timeout, self.retry, self.capacity = (
            clock,
            timeout,
            retry,
            capacity,
        )
        self.cache = OrderedDict()
        self.tasks, self.errors, self.observed = {}, {}, {}
        self.closed = False

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "CREATE TABLE IF NOT EXISTS private_order_events(id INTEGER PRIMARY KEY,venue TEXT,received_at REAL,payload TEXT)"
            )
            await d.commit()

    async def ingest(self, venue, rows):
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("PRIVATE_EVENT_BATCH_INVALID")
        accepted = []
        preview = dict(self.cache)
        for row in rows:
            if (
                not isinstance(row, dict)
                or not row.get("id")
                or not row.get("symbol")
                or row.get("side") not in ("buy", "sell")
            ):
                raise ValueError("PRIVATE_EVENT_IDENTITY_INVALID")
            amount, filled = row.get("amount"), row.get("filled")
            if any(
                isinstance(v, bool)
                or v is None
                or not math.isfinite(float(v))
                or float(v) < 0
                for v in (amount, filled)
            ) or float(filled) > float(amount):
                raise ValueError("PRIVATE_EVENT_VOLUME_INVALID")
            parsed = Reader(venue, self.clients[venue]).parse(row)
            key = (venue, str(row["id"]))
            old = preview.get(key)
            if old:
                previous = old[1]
                if (row["symbol"], row["side"], float(amount)) != (
                    previous["symbol"],
                    previous["side"],
                    float(previous["amount"]),
                ):
                    raise ValueError("PRIVATE_EVENT_ORDER_CONFLICT")
                if parsed.filled < float(previous["filled"]):
                    continue
                previous_result = Reader(venue, self.clients[venue]).parse(previous)
                if previous_result.status in (
                    "FILLED",
                    "CANCELED",
                    "REJECTED",
                    "FAILED",
                ) and parsed.status not in ("FILLED", "CANCELED", "REJECTED", "FAILED"):
                    continue
            # No native raw info or credential-bearing fields reach persistence.
            clean = {
                k: row.get(k)
                for k in (
                    "id",
                    "symbol",
                    "side",
                    "amount",
                    "filled",
                    "status",
                    "average",
                    "fee",
                    "fees",
                    "clientOrderId",
                    "clientOrderID",
                )
            }
            clean["fee"] = (
                {"cost": parsed.fee, "currency": "USDT"}
                if parsed.fee is not None and (row.get("fee") or row.get("fees"))
                else None
            )
            clean["fees"] = None
            if old and float(clean["filled"]) == float(old[1]["filled"]):
                if clean["fee"] is None:
                    clean["fee"] = old[1].get("fee")
                if clean["average"] is None:
                    clean["average"] = old[1].get("average")
                if clean == old[1]:
                    continue
            accepted.append((key, clean))
            preview[key] = (self.clock(), clean)
        received = self.clock()
        async with aiosqlite.connect(self.path) as d:
            await d.executemany(
                "INSERT INTO private_order_events(venue,received_at,payload) VALUES(?,?,?)",
                [
                    (venue, received, json.dumps(row, allow_nan=False))
                    for _, row in accepted
                ],
            )
            await d.execute(
                "DELETE FROM private_order_events WHERE id IN (SELECT id FROM private_order_events ORDER BY id DESC LIMIT -1 OFFSET 50000)"
            )
            await d.commit()
        # Publish only after durable write. A failed write does not grant cache truth.
        for key, row in accepted:
            self.cache[key] = (received, row)
            self.cache.move_to_end(key)
            while len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
        self.observed[venue] = received
        if accepted and self.on_event:
            self.on_event()

    def get(self, venue, order_id, symbol, max_age=2):
        item = self.cache.get((venue, str(order_id)))
        if item is None or venue in self.errors:
            return None
        received, row = item
        if (
            not 0 <= self.clock() - received <= max_age
            or row["symbol"] != symbol
            or row.get("fee") is None
        ):
            return None
        parsed = Reader(venue, self.clients[venue]).parse(row)
        if parsed.filled > 0 and (parsed.avg_price is None or parsed.fee is None):
            return None
        return parsed

    async def _watch(self, venue, client):
        while not self.closed:
            try:
                rows = await asyncio.wait_for(client.watch_orders(), self.timeout)
                await self.ingest(venue, rows)
                self.errors.pop(venue, None)
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.errors[venue] = type(error).__name__
                for key in list(self.cache):
                    if key[0] == venue:
                        self.cache.pop(key)
                if self.on_event:
                    self.on_event()
                await asyncio.sleep(self.retry)

    def start(self):
        for venue, client in self.clients.items():
            if client.has.get("watchOrders") is True and venue not in self.tasks:
                self.tasks[venue] = asyncio.create_task(self._watch(venue, client))

    async def close(self):
        self.closed = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.cache.clear()


class StreamReader(Reader):
    def __init__(self, venue, client, streams):
        super().__init__(venue, client)
        self.streams = streams

    async def order(self, order_id, symbol):
        cached = self.streams.get(self.venue, order_id, symbol)
        return cached if cached is not None else await super().order(order_id, symbol)
