"""Existing SafeExecutor boundary plus terminal native-contract accounting.

No client factory, credentials, startup writes or account acceptance here.
The caller must provide isolated one-way linear USDT futures and admission.
"""

import asyncio
import time
from decimal import Decimal, localcontext
from .dex_live_bridge import dec
from .spot_future_live_session import Session as CashSession
from .exchange_executor import SubmitRequest
from .native_order_plan import market, validate_request, format_value
from .private_reader import PrivateReader
from .private_order_reader import Reader as OrderReader
from .durable_order_reconcile import reconcile_and_persist, TERMINAL
from .recovery_market import Reader as RecoveryReader
from .spot_future_live_preflight import quote


def snapshot(intents):
    import json

    return sorted(
        [
            iid,
            row["_journal_sequence"],
            json.dumps(
                {k: v for k, v in row.items() if k != "_journal_sequence"},
                sort_keys=True,
            ),
        ]
        for iid, row in intents.items()
    )


def rebuild(intents, tid, p):
    size, base, cost, realized, fees = (
        dec(p.contract_size),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )
    stages, seen = [], set()
    sequences = [r.get("_journal_sequence") for r in intents.values()]
    if any(type(x) is not int or x <= 0 for x in sequences) or len(
        set(sequences)
    ) != len(sequences):
        raise ValueError("DEX_CEX_SEQUENCE_UNKNOWN")
    entry_side = "sell" if p.direction == "forward" else "buy"
    with localcontext() as ctx:
        ctx.prec = 80
        for iid, row in sorted(
            intents.items(), key=lambda x: x[1]["_journal_sequence"]
        ):
            prefix = tid + ":cash:"
            if (
                row.get("trade_id") != tid
                or row.get("intent_id") != iid
                or not iid.startswith(prefix)
                or row.get("venue") != p.venue
                or row.get("symbol") != p.symbol
                or row.get("state") not in TERMINAL
                or row.get("base_currency") is not None
                or row.get("base_fee", 0) != 0
            ):
                raise ValueError("DEX_CEX_TERMINAL_SCOPE")
            stage = iid[len(prefix) :]
            entry = stage == "hedge"
            if (
                not entry
                and stage != "exit"
                and stage not in ("recovery-1", "recovery-2", "recovery-3")
            ):
                raise ValueError("DEX_CEX_STAGE_INVALID")
            if (
                row.get("side")
                != (entry_side if entry else "buy" if entry_side == "sell" else "sell")
                or type(row.get("reduce_only")) not in (int, bool)
                or row["reduce_only"] not in (0, 1)
                or bool(row["reduce_only"]) != (not entry)
            ):
                raise ValueError("DEX_CEX_DIRECTION_INVALID")
            stages.append(stage)
            q, requested, fee = (
                dec(row.get("filled")),
                dec(row.get("qty")),
                dec(row.get("fee")),
            )
            if q < 0 or requested <= 0 or q > requested or fee < 0:
                raise ValueError("DEX_CEX_FILL_OR_FEE_INVALID")
            if q == 0:
                if fee:
                    raise ValueError("DEX_CEX_ZERO_FILL_COST")
                continue
            oid = row.get("order_id")
            if not oid or oid in seen:
                raise ValueError("DEX_CEX_DUPLICATE_ORDER")
            seen.add(oid)
            price = dec(row.get("avg_price"))
            if price <= 0:
                raise ValueError("DEX_CEX_PRICE_INVALID")
            q *= size
            fees += fee
            if entry:
                base += q
                cost += q * price
            else:
                if q > base:
                    raise ValueError("DEX_CEX_CLOSE_OVERFILL")
                average = cost / base
                realized += q * (average - price) * (1 if entry_side == "sell" else -1)
                cost -= q * average
                base -= q
    return dict(
        base=str(base * (-1 if p.direction == "forward" else 1)),
        entry_price=str(cost / base) if base else None,
        realized=str(realized),
        fees=str(fees),
        stages=stages,
    )


class Backend:
    def __init__(
        self, store, diary, clients, entry_authority, exit_authority, clock=time.time
    ):
        self.store, self.diary, self.clients, self.clock = store, diary, clients, clock
        self.session = CashSession(
            store, diary, None, entry_authority, exit_authority, clock=clock
        )

    async def prepare(self, p, signed_raw, closing):
        client = self.clients[p.venue]
        m = market(client, p.symbol)
        if dec(m["contractSize"]) != dec(p.contract_size):
            raise ValueError("DEX_CEX_CONTRACT_CHANGED")
        with localcontext() as ctx:
            ctx.prec = 80
            target = abs(dec(signed_raw)) / 10**p.asset_decimals / dec(p.contract_size)
            qty = format_value(client, p.symbol, target, "amount")
            # This lifecycle requires exact token restoration, not a hidden dust tolerance.
            if qty != target:
                raise ValueError("DEX_CEX_NATIVE_RESIDUAL_UNREPRESENTABLE")
        side = "buy" if signed_raw > 0 else "sell"
        if closing:
            r = SubmitRequest(p.symbol, side, float(qty), "market", reduce_only=True)
            r = await RecoveryReader({p.venue: client}, clock=self.clock).quote(
                p.venue, r, float(dec(p.contract_size))
            )
        else:
            book = await asyncio.wait_for(
                client.fetch_order_book(p.symbol, limit=20), 8
            )
            reference = book["asks" if side == "buy" else "bids"][0][0]
            price = format_value(client, p.symbol, dec(reference), "price")
            r = SubmitRequest(
                p.symbol, side, float(qty), "limit", float(price), False, True
            )
            r = await quote(
                client, p.venue, r, float(dec(p.contract_size)), clock=self.clock
            )
        validate_request(client, r)
        return r

    async def send(self, tid, stage, p, request, closing):
        return await self.session._send(
            tid, stage, p.venue, self.clients[p.venue], request, closing=closing
        )

    async def reconcile(self, tid, p):
        client = self.clients[p.venue]
        _, unresolved = await reconcile_and_persist(
            self.diary, {p.venue: OrderReader(p.venue, client)}, tid
        )
        if unresolved:
            raise ValueError("DEX_CEX_ORDER_UNRESOLVED")
        intents = await self.diary.order_intents(tid)
        flow = rebuild(intents, tid, p)
        start = self.clock()
        positions, orders = await asyncio.gather(
            PrivateReader(p.venue, client).positions(),
            PrivateReader(p.venue, client).orders(),
        )
        if orders or not 0 <= self.clock() - start <= 15:
            raise ValueError("DEX_CEX_WORKING_ORDER_OR_STALE")
        actual = Decimal(0)
        for pos in positions:
            if (
                pos.venue != p.venue
                or pos.symbol != p.symbol
                or pos.side != ("short" if p.direction == "forward" else "long")
                or dec(pos.contract_size) != dec(p.contract_size)
            ):
                raise ValueError("DEX_CEX_UNMANAGED_POSITION")
            actual += dec(pos.contracts) * dec(p.contract_size)
        expected = abs(dec(flow["base"]))
        if abs(actual - expected) > expected * Decimal("1e-10"):
            raise ValueError("DEX_CEX_PRIVATE_MISMATCH")
        return dict(flow, verified=True, ts=start, journal_snapshot=snapshot(intents))
