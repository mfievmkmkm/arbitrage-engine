"""Spot write adapter and read-only lookup with asset-denominated fee truth."""

from .ccxt_executor import CCXTExecutor
from .exchange_executor import SubmitResult
from .private_order_reader import Reader as OrderReader
from .spot_native_order import market, validate_request


def parse(client, row):
    if (
        not isinstance(row, dict)
        or not row.get("id")
        or row.get("amount") is None
        or row.get("filled") is None
    ):
        raise ValueError("SPOT_ORDER_IDENTITY_UNKNOWN")
    if isinstance(row.get("average"), bool):
        raise ValueError("SPOT_PRICE_INVALID")
    m = market(client, row.get("symbol"))
    parts = [row["fee"]] if row.get("fee") else row.get("fees") or []
    if not isinstance(parts, list):
        raise ValueError("SPOT_FEES_INVALID")
    quote_fee, base_fee = 0.0, 0.0
    for p in parts:
        if not isinstance(p, dict):
            raise ValueError("SPOT_FEES_INVALID")
        currency = str(p.get("currency") or "").upper()
        # Signed fees/rebates are explicit evidence, never estimated conversion.
        raw = p.get("cost")
        if isinstance(raw, bool) or raw is None:
            raise ValueError("SPOT_FEE_INVALID")
        import math

        cost = float(raw)
        if not math.isfinite(cost):
            raise ValueError("SPOT_FEE_INVALID")
        if currency == m["base"].upper():
            base_fee += cost
        elif currency == "USDT":
            quote_fee += cost
        else:
            raise ValueError("SPOT_FEE_CONVERSION_REQUIRED")
    # Reuse fill/status/price validation with the converted QUOTE-only fee.
    converted = dict(
        row, fee=None, fees=[dict(currency="USDT", cost=quote_fee)] if parts else []
    )
    r = CCXTExecutor("parse", client)._result(converted)
    if abs(base_fee) > r.filled:
        raise ValueError("SPOT_BASE_FEE_EXCEEDS_FILL")
    if r.filled == 0 and (quote_fee != 0 or base_fee != 0):
        raise ValueError("SPOT_ZERO_FILL_FEE_CONFLICT")
    return SubmitResult(
        r.order_id, r.status, r.filled, r.avg_price, r.fee, base_fee, m["base"]
    )


class SpotExecutor(CCXTExecutor):
    def validate(self, request):
        from .spot_quote_evidence import validate

        validate(request, self.venue)
        return validate_request(self.client, request)

    def _result(self, row):
        return parse(self.client, row)


class SpotOrderReader(OrderReader):
    def parse(self, row):
        return parse(self.client, row)
