"""Read-only loaded-market checks. A valid plan is not trading permission."""

import math
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext, ROUND_FLOOR
from math import lcm
from .exchange_executor import SubmitRequest


def number(value, name, positive=True):
    try:
        if isinstance(value, bool):
            raise ValueError()
        x = Decimal(str(value))
        if (
            not x.is_finite()
            or not math.isfinite(float(x))
            or (x <= 0 or float(x) <= 0 if positive else x < 0)
        ):
            raise ValueError()
        return x
    except Exception:
        raise ValueError(name + "_INVALID")


def market(client, symbol):
    try:
        m = client.market(symbol)
    except Exception:
        raise ValueError("LOADED_MARKET_REQUIRED")
    if (
        m.get("symbol") != symbol
        or m.get("contract") is not True
        or m.get("linear") is not True
        or m.get("inverse") is True
        or m.get("settle") != "USDT"
        or m.get("quote") != "USDT"
        or not m.get("base")
        or m.get("active") is False
    ):
        raise ValueError("NATIVE_INSTRUMENT_UNVERIFIED")
    number(m.get("contractSize"), "CONTRACT_SIZE")
    p = m.get("precision") or {}
    if p.get("amount") is None or p.get("price") is None:
        raise ValueError("NATIVE_PRECISION_UNKNOWN")
    limits = m.get("limits") or {}
    if all((limits.get(k) or {}).get("min") is None for k in ("amount", "cost")):
        raise ValueError("NATIVE_ORDER_MINIMUM_UNKNOWN")
    if getattr(client, "precisionMode", None) not in (2, 3, 4):
        raise ValueError("NATIVE_PRECISION_MODE_UNKNOWN")
    return m


def format_value(client, symbol, value, kind):
    try:
        result = getattr(client, kind + "_to_precision")(symbol, str(value))
    except Exception:
        raise ValueError("NATIVE_" + kind.upper() + "_FORMAT_FAILED")
    return number(result, "FORMATTED_" + kind.upper())


def bounds(m, kind, value):
    limits = (m.get("limits") or {}).get(kind) or {}
    for key in ("min", "max"):
        if limits.get(key) is not None:
            limit = number(
                limits[key], "LIMIT_" + kind.upper() + "_" + key.upper(), positive=False
            )
            if (key == "min" and value < limit) or (key == "max" and value > limit):
                raise ValueError("NATIVE_" + kind.upper() + "_" + key.upper())


def validate_request(client, r):
    if r.side not in ("buy", "sell") or r.order_type not in ("limit", "market"):
        raise ValueError("REQUEST_TYPE_INVALID")
    if type(r.reduce_only) is not bool or type(r.ioc) is not bool:
        raise ValueError("REQUEST_FLAGS_INVALID")
    m = market(client, r.symbol)
    qty = number(r.qty, "REQUEST_QTY")
    if format_value(client, r.symbol, qty, "amount") != qty:
        raise ValueError("REQUEST_AMOUNT_NOT_NATIVE")
    if r.order_type == "market":
        if r.price is not None or r.ioc:
            raise ValueError("MARKET_REQUEST_PARAMS_INVALID")
        price = number(r.reference_price, "MARKET_REFERENCE_PRICE")
    else:
        price = number(r.price, "REQUEST_PRICE")
        if format_value(client, r.symbol, price, "price") != price:
            raise ValueError("REQUEST_PRICE_NOT_NATIVE")
    bounds(m, "amount", qty)
    bounds(m, "price", price)
    bounds(m, "cost", qty * number(m["contractSize"], "CONTRACT_SIZE") * price)
    return r


@dataclass(frozen=True)
class PairPlan:
    valid: bool
    reason: str
    base_qty: float = 0
    long: SubmitRequest | None = None
    short: SubmitRequest | None = None
    evidence: str = "PUBLIC_NATIVE_METADATA_NOT_ACCOUNT_CERTIFICATION"
    release_authorized: bool = False

    def row(self):
        return asdict(self)


def quantum(client, m):
    mode = client.precisionMode
    raw = m["precision"]["amount"]
    p = (
        Decimal(str(raw))
        if mode == 2
        else number(raw, "AMOUNT_PRECISION", positive=False)
    )
    if not p.is_finite() or abs(p) > 100:
        raise ValueError("AMOUNT_PRECISION_INVALID")
    if mode == 4:
        return number(p, "AMOUNT_TICK") * number(m["contractSize"], "CONTRACT_SIZE")
    if mode == 2:
        if p != p.to_integral_value():
            raise ValueError("DECIMAL_PRECISION_INVALID")
        return Decimal(10) ** (-int(p)) * number(m["contractSize"], "CONTRACT_SIZE")
    return None


def prepare_pair(
    symbol,
    long_venue,
    short_venue,
    long_client,
    short_client,
    requested_base,
    long_price,
    short_price,
    max_price_change_pct=0.2,
):
    try:
        if long_venue == short_venue:
            raise ValueError("VENUE_PAIR_INVALID")
        with localcontext() as ctx:
            ctx.prec = 50
            a, b = market(long_client, symbol), market(short_client, symbol)
            if any(a.get(k) != b.get(k) for k in ("base", "quote", "settle")):
                raise ValueError("NATIVE_INSTRUMENT_MISMATCH")
            qty = number(requested_base, "BASE_QTY")
            sizes = [number(x["contractSize"], "CONTRACT_SIZE") for x in (a, b)]
            steps = [quantum(c, m) for c, m in ((long_client, a), (short_client, b))]
            if all(x is not None for x in steps):
                scale = 10 ** max(0, *(-x.as_tuple().exponent for x in steps))
                common = Decimal(lcm(*(int(x * scale) for x in steps))) / scale
                qty = (qty / common).to_integral_value(rounding=ROUND_FLOOR) * common
            clients = (long_client, short_client)
            contracts = []
            for _ in range(16):
                contracts = [
                    format_value(c, symbol, qty / s, "amount")
                    for c, s in zip(clients, sizes)
                ]
                amounts = [q * s for q, s in zip(contracts, sizes)]
                if any(x > qty for x in amounts):
                    raise ValueError("NATIVE_ROUNDING_INCREASES_EXPOSURE")
                if amounts[0] == amounts[1]:
                    qty = amounts[0]
                    break
                qty = min(amounts)
            else:
                raise ValueError("NATIVE_EXPOSURE_NOT_ALIGNED")
            cap = number(max_price_change_pct, "PRICE_CHANGE_CAP", positive=False) / 100
            prices = [number(x, "REFERENCE_PRICE") for x in (long_price, short_price)]
            native = [
                format_value(c, symbol, p, "price") for c, p in zip(clients, prices)
            ]
            if any(abs(n - p) / p > cap for n, p in zip(native, prices)):
                raise ValueError("NATIVE_PRICE_CHANGE_TOO_LARGE")
            reqs = [
                SubmitRequest(symbol, side, float(q), "limit", float(p), False, True)
                for side, q, p in zip(("buy", "sell"), contracts, native)
            ]
            for c, r in zip(clients, reqs):
                validate_request(c, r)
            return PairPlan(True, "OK", float(qty), *reqs)
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        return PairPlan(False, str(error))
