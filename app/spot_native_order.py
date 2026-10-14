"""Explicit cash-market validation. Never weaken derivative validation."""

from .native_order_plan import number, format_value, bounds


def market(client, symbol):
    try:
        m = client.market(symbol)
    except Exception:
        raise ValueError("SPOT_LOADED_MARKET_REQUIRED")
    if (
        m.get("symbol") != symbol
        or m.get("spot") is not True
        or m.get("contract") is not False
        or m.get("quote") != "USDT"
        or not isinstance(m.get("base"), str)
        or not m["base"]
        or m.get("active") is not True
        or m.get("settle") is not None
        or getattr(client, "precisionMode", None) not in (2, 3, 4)
    ):
        raise ValueError("SPOT_INSTRUMENT_UNVERIFIED")
    p = m.get("precision") or {}
    if p.get("amount") is None or p.get("price") is None:
        raise ValueError("SPOT_PRECISION_UNKNOWN")
    limits = m.get("limits") or {}
    if all((limits.get(k) or {}).get("min") is None for k in ("amount", "cost")):
        raise ValueError("SPOT_MINIMUM_UNKNOWN")
    return m


def validate_request(client, r):
    # Cash close has no reduceOnly semantics; only bounded IOC limit is supported.
    if r.side not in ("buy", "sell") or r.order_type != "limit":
        raise ValueError("SPOT_IOC_LIMIT_REQUIRED")
    if r.reduce_only is not False or r.ioc is not True or r.reference_price is not None:
        raise ValueError("SPOT_REQUEST_FLAGS_INVALID")
    m = market(client, r.symbol)
    qty, price = number(r.qty, "SPOT_QTY"), number(r.price, "SPOT_PRICE")
    if format_value(client, r.symbol, qty, "amount") != qty:
        raise ValueError("SPOT_AMOUNT_NOT_NATIVE")
    if format_value(client, r.symbol, price, "price") != price:
        raise ValueError("SPOT_PRICE_NOT_NATIVE")
    bounds(m, "amount", qty)
    bounds(m, "price", price)
    bounds(m, "cost", qty * price)
    return r
