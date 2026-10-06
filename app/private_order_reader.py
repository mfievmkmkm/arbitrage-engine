"""Read-only order evidence. This class deliberately has no submit/cancel methods."""

import asyncio
import math
from .ccxt_executor import client_id
from .exchange_executor import SubmitResult
from .order_status import normalize


class Reader:
    def __init__(self, venue, client, timeout=8):
        self.venue = venue
        self.client = client
        self.timeout = timeout

    def parse(self, row):
        filled = float(row.get("filled") or 0)
        if not math.isfinite(filled) or filled < 0:
            raise ValueError("INVALID_FILLED")
        amount = row.get("amount")
        status = normalize(
            row.get("status"), filled, float(amount) if amount is not None else None
        )
        average = row.get("average")
        if average is not None:
            average = float(average)
            if not math.isfinite(average) or average <= 0:
                average = None
        parts = [row["fee"]] if row.get("fee") else (row.get("fees") or [])
        fee = 0.0 if filled == 0 else None
        if parts and all(
            p.get("cost") is not None
            and str(p.get("currency", "")).upper() in ("USDT", "USD")
            for p in parts
        ):
            fee = sum(float(p["cost"]) for p in parts)
            if not math.isfinite(fee):
                fee = None
        return SubmitResult(str(row.get("id") or ""), status, filled, average, fee)

    async def order(self, order_id, symbol):
        row = await asyncio.wait_for(
            self.client.fetch_order(order_id, symbol), self.timeout
        )
        if str(row.get("id") or "") != str(order_id) or row.get("symbol") != symbol:
            raise ValueError("ORDER_RESPONSE_IDENTITY_MISMATCH")
        return self.parse(row)

    async def order_by_client_id(self, intent_id, symbol):
        wanted = client_id(intent_id)
        methods = ("fetch_orders", "fetch_open_orders", "fetch_closed_orders")
        for method in methods:
            fn = getattr(self.client, method, None)
            if fn is None:
                continue
            try:
                rows = await asyncio.wait_for(fn(symbol), self.timeout)
            except Exception:
                continue
            matches = [
                r
                for r in rows
                if str(r.get("clientOrderId") or r.get("clientOrderID") or "") == wanted
                and r.get("symbol") == symbol
            ]
            if len(matches) > 1:
                raise ValueError("CLIENT_ID_NOT_UNIQUE")
            if matches:
                return self.parse(matches[0])
        # Not found cannot prove that a timed-out request was rejected by the venue.
        raise LookupError("CLIENT_ORDER_NOT_FOUND")
