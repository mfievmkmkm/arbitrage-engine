"""Read-only executable recovery quotes; never grants trading authority."""

import asyncio
import math
import time
from dataclasses import replace
from decimal import Decimal, localcontext
from .native_order_plan import market, number, validate_request

MAX_AGE = 1.5
MAX_SLIPPAGE = 0.002


def levels(rows, side):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("RECOVERY_BOOK_EMPTY")
    out = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError("RECOVERY_BOOK_LEVEL_INVALID")
        p, q = float(number(row[0], "BOOK_PRICE")), float(number(row[1], "BOOK_AMOUNT"))
        if out and (p >= out[-1][0] if side == "bids" else p <= out[-1][0]):
            raise ValueError("RECOVERY_BOOK_ORDER_INVALID")
        out.append([p, q])
    return out


def executable(rows, contracts):
    with localcontext() as ctx:
        ctx.prec = 50
        remaining, cost, worst = Decimal(str(contracts)), Decimal(0), None
        for price, available in rows:
            take = min(remaining, Decimal(str(available)))
            cost += take * Decimal(str(price))
            remaining -= take
            worst = price
            if remaining == 0:
                return float(cost / Decimal(str(contracts))), worst
    raise ValueError("RECOVERY_LIQUIDITY_LOW")


def validate_evidence(request, venue, now=None):
    e = request.market_evidence
    if not isinstance(e, dict) or e.get("source") != "PUBLIC_REST_RECOVERY_V1":
        raise ValueError("RECOVERY_EVIDENCE_REQUIRED")
    if request.order_type != "market" or request.price is not None or request.ioc:
        raise ValueError("RECOVERY_REQUEST_TYPE_INVALID")
    expected = (venue, request.symbol, request.side, request.qty, request.reduce_only)
    actual = tuple(
        e.get(k) for k in ("venue", "symbol", "side", "contracts", "reduce_only")
    )
    if actual != expected or type(e.get("reduce_only")) is not bool:
        raise ValueError("RECOVERY_EVIDENCE_REQUEST_MISMATCH")
    now = time.time() if now is None else now
    times = [
        float(number(e.get(k), k)) for k in ("book_ts", "started_at", "received_at")
    ]
    book_ts, start, received = times
    if (
        not math.isfinite(now)
        or received < start
        or book_ts > received
        or received > now
        or now - min(book_ts, start) > MAX_AGE
    ):
        raise ValueError("RECOVERY_BOOK_STALE")
    bids, asks = levels(e.get("bids"), "bids"), levels(e.get("asks"), "asks")
    if bids[0][0] >= asks[0][0]:
        raise ValueError("RECOVERY_BOOK_CROSSED")
    contracts = float(number(request.qty, "RECOVERY_CONTRACTS"))
    size = float(number(e.get("contract_size"), "CONTRACT_SIZE"))
    if not math.isclose(
        float(number(e.get("base_qty"), "BASE_QTY")), contracts * size, rel_tol=1e-12
    ):
        raise ValueError("RECOVERY_BASE_QTY_MISMATCH")
    rows = asks if request.side == "buy" else bids if request.side == "sell" else None
    if rows is None:
        raise ValueError("RECOVERY_SIDE_INVALID")
    price, worst = executable(rows, contracts)
    best = rows[0][0]
    if abs(worst - best) / best > MAX_SLIPPAGE:
        raise ValueError("RECOVERY_BOOK_SLIPPAGE_TOO_HIGH")
    if not math.isclose(
        float(number(request.reference_price, "REFERENCE_PRICE")), price, rel_tol=1e-12
    ):
        raise ValueError("RECOVERY_REFERENCE_MISMATCH")
    return e


class Reader:
    def __init__(self, public_clients, timeout=8, clock=time.time):
        self.clients, self.timeout, self.clock = public_clients, timeout, clock

    async def quote(self, venue, request, contract_size):
        client = self.clients.get(venue)
        if client is None:
            raise ValueError("RECOVERY_VENUE_MISSING")
        m = market(client, request.symbol)
        size = float(number(contract_size, "CONTRACT_SIZE"))
        if not math.isclose(size, float(m["contractSize"]), rel_tol=1e-12):
            raise ValueError("RECOVERY_CONTRACT_SIZE_MISMATCH")
        started = self.clock()
        book = await asyncio.wait_for(
            client.fetch_order_book(request.symbol, limit=20), self.timeout
        )
        received = self.clock()
        if book.get("symbol") not in (None, request.symbol):
            raise ValueError("RECOVERY_BOOK_SYMBOL_MISMATCH")
        stamp = book.get("timestamp")
        ts = started if stamp is None else float(number(stamp, "BOOK_TIMESTAMP")) / 1000
        bids, asks = levels(book.get("bids"), "bids"), levels(book.get("asks"), "asks")
        price, _ = executable(
            asks if request.side == "buy" else bids,
            float(number(request.qty, "RECOVERY_CONTRACTS")),
        )
        evidence = dict(
            source="PUBLIC_REST_RECOVERY_V1",
            venue=venue,
            symbol=request.symbol,
            side=request.side,
            contracts=request.qty,
            reduce_only=request.reduce_only,
            contract_size=size,
            base_qty=request.qty * size,
            book_ts=ts,
            started_at=started,
            received_at=received,
            bids=bids,
            asks=asks,
        )
        prepared = replace(request, reference_price=price, market_evidence=evidence)
        validate_evidence(prepared, venue, self.clock())
        validate_request(client, prepared)
        return prepared


async def prepare(reader, venue, request, size, timeout=8):
    if reader is None:
        return request
    prepared = await asyncio.wait_for(reader.quote(venue, request, size), timeout)
    if any(
        getattr(prepared, k, None) != getattr(request, k)
        for k in (
            "symbol",
            "side",
            "qty",
            "order_type",
            "price",
            "reduce_only",
            "ioc",
            "client_order_id",
        )
    ):
        raise ValueError("RECOVERY_QUOTE_CHANGED_REQUEST")
    e = validate_evidence(
        prepared, venue, reader.clock() if hasattr(reader, "clock") else None
    )
    if not math.isclose(
        float(number(size, "CONTRACT_SIZE")), e["contract_size"], rel_tol=1e-12
    ):
        raise ValueError("RECOVERY_CONTRACT_SIZE_MISMATCH")
    return prepared


def actual_slippage(request, result):
    if request.market_evidence is None or result.filled <= 0:
        return False
    ref = request.reference_price
    return (
        result.avg_price > ref * (1 + MAX_SLIPPAGE)
        if request.side == "buy"
        else result.avg_price < ref * (1 - MAX_SLIPPAGE)
    )
