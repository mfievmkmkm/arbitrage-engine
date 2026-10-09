"""Bounded residual exits; every round has a durable, non-repeatable claim."""

import asyncio
import json
import math
from dataclasses import replace
import time
import aiosqlite
from .runtime_state import RuntimeTrade
from .exit_residual_evidence import evaluate, fingerprint
from .exchange_executor import SubmitRequest
from .live_order_intent import OrderIntent
from .recovery_market import prepare, actual_slippage
from .order_settlement import settle


class Coordinator:
    def __init__(
        self,
        durable,
        diary,
        executors,
        snapshots,
        market,
        authority,
        stop,
        clock=time.time,
        max_rounds=3,
        timeout=8,
    ):
        self.durable, self.diary, self.executors = durable, diary, executors
        self.snapshots, self.market, self.authority, self.stop = (
            snapshots,
            market,
            authority,
            stop,
        )
        self.clock, self.max_rounds, self.timeout = clock, max_rounds, timeout
        self.lock = asyncio.Lock()
        self.latest = []

    async def claim(self, trade, proof):
        """Compare the fill history under the same transaction as the claim."""
        async with aiosqlite.connect(self.durable.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?",
                (trade.trade_id,),
            ) as c:
                row = await c.fetchone()
            if row is None or row["phase"] != "EXIT_SUBMITTING":
                return None, "RESIDUAL_DURABLE_CHANGED"
            payload = json.loads(row["payload"])
            if (
                payload.get("runtime_trade") != trade.row()
                or payload.get("exit_hold_reason")
                or payload.get("entry_hold_reason")
            ):
                return None, "RESIDUAL_DURABLE_HOLD"
            async with db.execute(
                "SELECT * FROM order_intents WHERE trade_id=?", (trade.trade_id,)
            ) as c:
                intents = [dict(x) for x in await c.fetchall()]
            for x in intents:
                x.update(json.loads(x.get("payload") or "{}"))
            if fingerprint(intents) != proof.fingerprint:
                return None, "RESIDUAL_FILL_HISTORY_CHANGED"
            rounds = payload.get("exit_residual_rounds", [])
            if any(r["fingerprint"] == proof.fingerprint for r in rounds):
                return None, "RESIDUAL_ALREADY_CLAIMED"
            if rounds:
                from .durable_order_reconcile import TERMINAL

                by_id = {x["intent_id"]: x for x in intents}
                last = rounds[-1]
                for venue, side, qty in (
                    (trade.long_venue, "sell", last["long_contracts"]),
                    (trade.short_venue, "buy", last["short_contracts"]),
                ):
                    if qty:
                        iid = f"{trade.trade_id}:exit-residual:{last['sequence']}:{venue}:{side}"
                        if iid not in by_id or by_id[iid]["state"] not in TERMINAL:
                            return None, "RESIDUAL_ROUND_UNRESOLVED"
                        previous = by_id[iid]
                        if (
                            previous["venue"] != venue
                            or previous["symbol"] != trade.symbol
                            or previous["side"] != side
                            or not previous["reduce_only"]
                            or previous["qty"] != qty
                        ):
                            return None, "RESIDUAL_ROUND_EVIDENCE_MISMATCH"
            if len(rounds) >= self.max_rounds:
                return None, "RESIDUAL_ATTEMPT_LIMIT"
            if rounds and not (
                proof.closed_long > rounds[-1]["closed_long"]
                or proof.closed_short > rounds[-1]["closed_short"]
            ):
                return None, "RESIDUAL_NO_FILL_PROGRESS"
            sequence = len(rounds) + 1
            rounds.append(
                dict(
                    sequence=sequence,
                    fingerprint=proof.fingerprint,
                    long_contracts=proof.long_contracts,
                    short_contracts=proof.short_contracts,
                    closed_long=proof.closed_long,
                    closed_short=proof.closed_short,
                    claimed_at=self.clock(),
                )
            )
            payload["exit_residual_rounds"] = rounds
            await db.execute(
                "UPDATE live_trades SET payload=?,updated_at=? WHERE trade_id=?",
                (json.dumps(payload), self.clock(), trade.trade_id),
            )
            await db.commit()
            return sequence, "CLAIMED"

    async def _hold(self, tid, reason):
        self.stop.stop(reason)
        await self.durable.phase(tid, "EXIT_SUBMITTING", exit_hold_reason=reason)

    async def process(self, summary):
        async with self.lock:
            out = []
            if (
                not summary.get("reconciled")
                or not summary.get("private_verified")
                or summary.get("unknown_orders")
                or self.market is None
                or isinstance(summary.get("ts"), bool)
                or not isinstance(summary.get("ts"), (int, float))
                or not math.isfinite(summary["ts"])
                or not 0 <= self.clock() - summary["ts"] <= 15
            ):
                self.latest = out
                return out
            for row in await self.durable.active():
                if row["phase"] != "EXIT_SUBMITTING":
                    continue
                claimed = False
                claim_started = False
                tid = row["trade_id"]
                try:
                    payload = json.loads(row["payload"])
                    if payload.get("entry_hold_reason") or payload.get(
                        "exit_hold_reason"
                    ):
                        continue
                    trade = RuntimeTrade(**payload["runtime_trade"])
                    if not self.authority(trade):
                        continue
                    own = list((await self.diary.order_intents(tid)).values())
                    snapshot = await self.snapshots()
                    proof = evaluate(trade, row, own, snapshot, self.clock())
                    if not proof.long_contracts and not proof.short_contracts:
                        continue  # Private-flat and PnL are exclusively the monitor's work.
                    requests = []
                    for venue, side, qty, size in (
                        (
                            trade.long_venue,
                            "sell",
                            proof.long_contracts,
                            trade.long_contract_size,
                        ),
                        (
                            trade.short_venue,
                            "buy",
                            proof.short_contracts,
                            trade.short_contract_size,
                        ),
                    ):
                        if qty:
                            if venue not in self.executors:
                                raise ValueError("RESIDUAL_EXECUTOR_MISSING")
                            requests.append(
                                (
                                    venue,
                                    SubmitRequest(
                                        trade.symbol, side, qty, "market", None, True
                                    ),
                                    size,
                                )
                            )
                    quotes = await asyncio.gather(
                        *(
                            prepare(self.market, v, r, size, self.timeout)
                            for v, r, size in requests
                        )
                    )
                    if not self.authority(trade):
                        continue
                    claim_started = True
                    sequence, reason = await self.claim(trade, proof)
                    if sequence is None:
                        if reason not in (
                            "RESIDUAL_FILL_HISTORY_CHANGED",
                            "RESIDUAL_ALREADY_CLAIMED",
                            "RESIDUAL_ROUND_UNRESOLVED",
                            "RESIDUAL_DURABLE_CHANGED",
                        ):
                            await self._hold(tid, reason)
                        out.append(dict(trade_id=tid, status=reason, attempted=False))
                        continue
                    claimed = True
                    # Account truth is refreshed after durable IO, before any sends.
                    fresh = evaluate(
                        trade,
                        await self.durable.get(tid),
                        list((await self.diary.order_intents(tid)).values()),
                        await self.snapshots(),
                        self.clock(),
                    )
                    if fresh != proof or not self.authority(trade):
                        raise ValueError("RESIDUAL_PRE_SEND_STATE_CHANGED")

                    async def submit(venue, request):
                        iid = f"{tid}:exit-residual:{sequence}:{venue}:{request.side}"
                        intent = OrderIntent(
                            iid,
                            tid,
                            venue,
                            trade.symbol,
                            request.side,
                            request.qty,
                            True,
                        )
                        req = replace(request, client_order_id=iid)
                        ex = self.executors[venue]
                        r, status = await asyncio.wait_for(
                            ex.submit_intent(intent, req), self.timeout
                        )
                        if r is None:
                            return "RESIDUAL_ORDER_UNKNOWN:" + status
                        r, status = await settle(
                            ex, r, trade.symbol, req.qty, self.timeout
                        )
                        if r is None:
                            return "RESIDUAL_ORDER_UNKNOWN:" + status
                        if actual_slippage(req, r):
                            return "RESIDUAL_ACTUAL_SLIPPAGE_STOP"
                        return "RESIDUAL_FILLS_PENDING_PRIVATE"

                    results = await asyncio.gather(
                        *(submit(v, q) for (v, _, _), q in zip(requests, quotes)),
                        return_exceptions=True,
                    )
                    reason = next(
                        (
                            str(x) if isinstance(x, str) else "RESIDUAL_SUBMIT_UNKNOWN"
                            for x in results
                            if x != "RESIDUAL_FILLS_PENDING_PRIVATE"
                        ),
                        "RESIDUAL_FILLS_PENDING_PRIVATE",
                    )
                    if reason != "RESIDUAL_FILLS_PENDING_PRIVATE":
                        await self._hold(tid, reason)
                    else:
                        await self.durable.phase(
                            tid, "EXIT_SUBMITTING", exit_dispatch_status=reason
                        )
                    out.append(dict(trade_id=tid, status=reason, attempted=True))
                except asyncio.CancelledError:
                    if claimed or claim_started:
                        await asyncio.shield(
                            self._hold(tid, "RESIDUAL_DISPATCH_INTERRUPTED")
                        )
                    raise
                except Exception as e:
                    reason = (
                        str(e)
                        if isinstance(e, ValueError)
                        else "RESIDUAL_EVIDENCE_UNAVAILABLE"
                    )
                    if claimed or claim_started:
                        await self._hold(tid, reason)
                    out.append(dict(trade_id=tid, status=reason, attempted=False))
            self.latest = out
            return out
