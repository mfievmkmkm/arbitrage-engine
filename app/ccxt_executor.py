import asyncio, hashlib, math
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
        if isinstance(row.get("filled"), bool) or isinstance(row.get("amount"), bool):
            raise RuntimeError("ACTUAL_AMOUNT_INVALID")
        filled = float(row.get("filled") or 0)
        if not math.isfinite(filled) or filled < 0:
            raise RuntimeError("ACTUAL_FILL_INVALID")
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
        if not math.isfinite(fee_cost):
            raise RuntimeError("ACTUAL_FEE_INVALID")
        if amount is not None and (
            not math.isfinite(float(amount))
            or float(amount) < 0
            or filled > float(amount) + float(amount) * 1e-10
        ):
            raise RuntimeError("ACTUAL_AMOUNT_INVALID")
        status = normalize(
            row.get("status"), filled, float(amount) if amount is not None else None
        )
        average = row.get("average")
        if average is not None:
            average = float(average)
            if not math.isfinite(average) or average <= 0:
                raise RuntimeError("ACTUAL_PRICE_INVALID")
        if filled > 0 and average is None:
            raise RuntimeError("ACTUAL_FILL_PRICE_UNKNOWN")
        return SubmitResult(str(row.get("id") or ""), status, filled, average, fee_cost)

    def validate(self, request):
        from .native_order_plan import validate_request

        if request.market_evidence is not None:
            from .quote_order_evidence import validate as validate_evidence

            e = validate_evidence(request, self.venue)
            if not math.isclose(
                e["contract_size"],
                float(self.client.market(request.symbol)["contractSize"]),
                rel_tol=1e-12,
            ):
                raise ValueError("RECOVERY_CONTRACT_SIZE_MISMATCH")
        return validate_request(self.client, request)

    async def submit(self, r):
        self.validate(r)
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
        result = self._result(row)
        if (
            r.order_type == "limit"
            and result.filled > 0
            and (
                (r.side == "buy" and result.avg_price > r.price * (1 + 1e-12))
                or (r.side == "sell" and result.avg_price < r.price * (1 - 1e-12))
            )
        ):
            raise RuntimeError("ACTUAL_FILL_OUTSIDE_LIMIT")
        return result

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
