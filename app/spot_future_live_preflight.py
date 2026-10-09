"""Read-only account admission for forward cash-and-carry; no orders here."""

import asyncio
import math
import time
from dataclasses import dataclass, replace
from .native_order_plan import number, market as future_market, format_value
from .spot_native_order import market as spot_market
from .spot_future_native_plan import prepare as native, rate
from .spot_account_reader import Reader as CashReader
from .private_reader import PrivateReader
from .public_books import normalize
from .recovery_market import executable
from .quote_order_evidence import validate
from .funding_timing import window


async def quote(client, venue, request, size, spot=False, clock=time.time):
    start = clock()
    raw = await asyncio.wait_for(client.fetch_order_book(request.symbol, limit=20), 8)
    b = normalize(raw, request.symbol, start, clock(), 1.5)
    _, worst = executable(b["asks" if request.side == "buy" else "bids"], request.qty)
    price = float(
        format_value(client, request.symbol, number(worst, "IOC_LIMIT"), "price")
    )
    e = dict(
        source="PUBLIC_SPOT_IOC_V1" if spot else "PUBLIC_IOC_ENTRY_V1",
        market_type="spot" if spot else "future",
        venue=venue,
        symbol=request.symbol,
        side=request.side,
        contracts=request.qty,
        contract_size=size,
        base_qty=request.qty * size,
        book_ts=b["timestamp"] / 1000,
        started_at=start,
        received_at=b["received_at"],
        bids=b["bids"],
        asks=b["asks"],
    )
    result = replace(request, price=price, market_evidence=e)
    validate(result, venue, clock())
    return result


@dataclass(frozen=True)
class Prepared:
    venue: str
    base: str
    spot_symbol: str
    future_symbol: str
    contract_size: float
    baseline_total: float
    spot: object
    future: object
    spot_fee_rate: float
    future_fee_rate: float
    budget: float
    minimum_net: float
    loss_allowance: float
    private_started_at: float
    prepared_at: float

    def validate_time(self, now):
        if (
            not self.private_started_at <= self.prepared_at <= now
            or now - self.private_started_at > 15
        ):
            raise ValueError("CASH_ADMISSION_STALE")
        validate(self.spot, self.venue + ":spot", now)
        validate(self.future, self.venue, now)


class Admission:
    def __init__(
        self,
        spot_clients,
        future_clients,
        funding,
        bankroll=50,
        notional=5,
        minimum_net=0.05,
        safety_pct=0.1,
        hold_seconds=1200,
        clock=time.time,
    ):
        self.spot_clients, self.future_clients, self.funding = (
            spot_clients,
            future_clients,
            funding,
        )
        self.bankroll, self.notional, self.minimum = bankroll, notional, minimum_net
        self.safety, self.hold_seconds, self.clock = safety_pct, hold_seconds, clock

    async def prepare(self, op):
        if op.get("direction") != "LONG_SPOT_SHORT_FUTURE":
            raise ValueError("SPOT_BORROWING_UNVERIFIED")
        v, ss, fs = op["exchange"], op["spot_symbol"], op["future_symbol"]
        from .private_funding_reader import POLICY

        if v not in POLICY:
            raise ValueError("CASH_FUNDING_HISTORY_UNSUPPORTED")
        s, f = self.spot_clients[v], self.future_clients[v]
        sm, fm = spot_market(s, ss), future_market(f, fs)
        if sm["base"] != fm["base"] or op.get("base") != sm["base"]:
            raise ValueError("CASH_INSTRUMENT_MISMATCH")
        budget = min(
            float(number(self.notional, "NOTIONAL")),
            float(number(self.bankroll, "CAPITAL")) * 0.1,
        )
        if budget > 5:
            raise ValueError("CASH_MICRO_BUDGET_LIMIT")
        minimum = float(number(self.minimum, "MINIMUM_NET", positive=False))
        safety = float(number(self.safety, "SAFETY_PCT", positive=False))
        number(self.hold_seconds, "HOLD_SECONDS")
        if (
            any(c.has.get("fetchTradingFee") is not True for c in (s, f))
            or f.has.get("fetchPositionMode") is not True
            or f.has.get("fetchFundingHistory") is not True
        ):
            raise ValueError("CASH_ACCOUNT_CAPABILITY_UNVERIFIED")
        mode, sf, ff, funding = await asyncio.wait_for(
            asyncio.gather(
                f.fetch_position_mode(fs),
                s.fetch_trading_fee(ss),
                f.fetch_trading_fee(fs),
                self.funding.get(v, fs),
            ),
            10,
        )
        if mode.get("hedged") is not False:
            raise ValueError("CASH_ONE_WAY_REQUIRED")
        if sf.get("symbol") != ss or ff.get("symbol") != fs:
            raise ValueError("CASH_FEE_SCOPE_MISMATCH")
        for row in (sf, ff):
            rate(row.get("maker"))
            rate(row.get("taker"))
        fr = getattr(funding, "rate", None)
        if (
            getattr(funding, "exchange", None) != v
            or getattr(funding, "symbol", None) != fs
        ):
            raise ValueError("CASH_FUNDING_SCOPE_MISMATCH")
        if (
            isinstance(fr, bool)
            or fr is None
            or not math.isfinite(float(fr))
            or abs(float(fr)) >= 1
            or funding.interval_hours is None
        ):
            raise ValueError("CASH_FUNDING_UNKNOWN")
        number(funding.interval_hours, "FUNDING_INTERVAL")
        number(funding.next_ts, "FUNDING_TIMESTAMP")
        w = window(
            funding.next_ts,
            self.hold_seconds,
            funding.interval_hours,
            int(self.clock() * 1000),
        )
        if w.reason in ("UNKNOWN", "STALE", "INVALID"):
            raise ValueError("CASH_FUNDING_CALENDAR_UNKNOWN")
        loss = budget * (safety / 100 + max(0, -float(fr)) * w.periods)
        started = self.clock()
        cash, future_cash, positions = await asyncio.wait_for(
            asyncio.gather(
                CashReader(v + ":spot", s, clock=self.clock).snapshot([sm["base"]]),
                CashReader(v, f, clock=self.clock).snapshot([]),
                PrivateReader(v, f).positions(),
            ),
            10,
        )
        if cash.orders or future_cash.orders or positions:
            raise ValueError("CASH_ACCOUNT_NOT_IDLE")
        if cash.asset(sm["base"])["used"] != 0:
            raise ValueError("CASH_BASE_INVENTORY_LOCKED")
        # Conservative even if both clients share one unified USDT wallet.
        if (
            min(cash.asset("USDT")["free"], future_cash.asset("USDT")["free"])
            < budget * 2.4
        ):
            raise ValueError("CASH_SHARED_WALLET_BUFFER_LOW")
        book_started = self.clock()
        sbook, fbook = await asyncio.wait_for(
            asyncio.gather(
                s.fetch_order_book(ss, limit=20), f.fetch_order_book(fs, limit=20)
            ),
            8,
        )
        book_received = self.clock()
        sb = normalize(sbook, ss, book_started, book_received, 1.5)
        fb = normalize(fbook, fs, book_started, book_received, 1.5)
        size = float(fm["contractSize"])
        gross = min(budget / sb["asks"][0][0], budget / fb["bids"][0][0])
        p = native(s, f, ss, fs, gross, sb["asks"][0][0], fb["bids"][0][0], sf["taker"])
        if not p.valid:
            raise ValueError(p.reason)
        sr, frq = await asyncio.gather(
            quote(s, v + ":spot", p.spot, 1, True, self.clock),
            quote(f, v, p.future, size, False, self.clock),
        )
        if max(sr.qty * sr.price, frq.qty * size * frq.price) > budget:
            raise ValueError("CASH_QUOTED_BUDGET_EXCEEDED")
        # Residual inventory is valued at zero. Funding income is not forecast PnL.
        gross_edge = p.hedged_base * frq.price - sr.qty * sr.price
        fees = 2 * sr.qty * sr.price * float(
            sf["taker"]
        ) + 2 * p.hedged_base * frq.price * float(ff["taker"])
        if gross_edge - fees - loss < minimum:
            raise ValueError("CASH_NET_BELOW_MINIMUM")
        result = Prepared(
            v,
            sm["base"],
            ss,
            fs,
            size,
            cash.asset(sm["base"])["total"],
            sr,
            frq,
            float(sf["taker"]),
            float(ff["taker"]),
            budget,
            minimum,
            loss,
            started,
            self.clock(),
        )
        result.validate_time(self.clock())
        return result

    async def preview(self, op):
        try:
            p = await self.prepare(op)
            return dict(
                status="ACCOUNT_PREFLIGHT_OK",
                strategy="spot_futures",
                venue=p.venue,
                spot_qty=p.spot.qty,
                future_contracts=p.future.qty,
                budget=p.budget,
                release_authorized=False,
                writes=0,
            )
        except (Exception,) as error:
            return dict(
                status="BLOCKED", reason=str(error), release_authorized=False, writes=0
            )
