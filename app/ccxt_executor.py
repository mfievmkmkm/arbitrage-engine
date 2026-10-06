import asyncio, hashlib
from .exchange_executor import ExchangeExecutor, SubmitResult
from .order_status import normalize


def client_id(value):
    if value and (
        len(value) > 32
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in value
        )
    ):
        return "ae" + hashlib.sha256(value.encode()).hexdigest()[:30]
    return value


class CCXTExecutor(ExchangeExecutor):
    def __init__(self, venue, client, timeout=8):
        self.venue = venue
        self.client = client
        self.timeout = timeout

    def _result(self, row):
        filled = float(row.get("filled") or 0)
        amount = row.get("amount")
        fee = row.get("fee")
        fees = [fee] if fee else (row.get("fees") or [])
        if filled > 0 and not fees:
            raise RuntimeError("ACTUAL_FEE_UNKNOWN")
        if any(
            x.get("cost") is None
            or str(x.get("currency", "")).upper() not in ("USDT", "USD")
            for x in fees
        ):
            raise RuntimeError("ACTUAL_FEE_USD_CONVERSION_REQUIRED")
        fee_cost = sum(float(x["cost"]) for x in fees)
        status = normalize(
            row.get("status"), filled, float(amount) if amount is not None else None
        )
        average = row.get("average")
        if average is None and filled > 0 and row.get("cost") is not None:
            average = float(row["cost"]) / filled
        return SubmitResult(str(row.get("id") or ""), status, filled, average, fee_cost)

    async def submit(self, r):
        params = {}
        if r.reduce_only:
            params["reduceOnly"] = True
        if r.ioc:
            params["timeInForce"] = "IOC"
        if r.client_order_id:
            params["clientOrderId"] = client_id(r.client_order_id)
        row = await asyncio.wait_for(
            self.client.create_order(
                r.symbol, r.order_type, r.side, r.qty, r.price, params
            ),
            self.timeout,
        )
        return self._result(row)

    async def order_by_client_id(self, client_order_id, symbol):
        if not hasattr(self.client, "fetch_orders"):
            raise RuntimeError("CLIENT_ORDER_LOOKUP_UNSUPPORTED")
        rows = await asyncio.wait_for(self.client.fetch_orders(symbol), self.timeout)
        for row in rows:
            cid = str(row.get("clientOrderId") or row.get("clientOrderID") or "")
            if cid == client_id(client_order_id):
                return self._result(row)
        raise RuntimeError("CLIENT_ORDER_NOT_FOUND")

    async def cancel(self, order_id, symbol):
        row = await asyncio.wait_for(
            self.client.cancel_order(order_id, symbol), self.timeout
        )
        return self._result(row)

    async def order(self, order_id, symbol):
        row = await asyncio.wait_for(
            self.client.fetch_order(order_id, symbol), self.timeout
        )
        return self._result(row)
