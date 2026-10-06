import asyncio
from .exchange_executor import ExchangeExecutor
from .order_lifecycle import transition


class SafeExecutor(ExchangeExecutor):
    def __init__(self, venue, inner, diary, gate, exit_gate=None):
        self.venue = venue
        self.inner = inner
        self.diary = diary
        self.gate = gate
        self.exit_gate = exit_gate or gate

    async def submit_intent(self, intent, request):
        if (
            intent.venue != self.venue
            or intent.symbol != request.symbol
            or intent.side != request.side
            or intent.qty != request.qty
            or intent.reduce_only != request.reduce_only
        ):
            return None, "INTENT_REQUEST_MISMATCH"
        states = await self.diary.order_intent_states(intent.trade_id)
        if intent.intent_id in states:
            return None, "DUPLICATE_OR_UNRESOLVED_INTENT"
        if not (self.exit_gate() if request.reduce_only else self.gate()):
            return None, "LIVE_GATE_LOCKED"
        if hasattr(self.diary, "claim_order_intent"):
            if not await self.diary.claim_order_intent(intent):
                return None, "DUPLICATE_OR_UNRESOLVED_INTENT"
        else:
            await self.diary.save_order_intent(intent, "SUBMITTING")
        try:
            result = await self.inner.submit(request)
        except (Exception, asyncio.CancelledError) as error:
            await asyncio.shield(self.diary.save_order_intent(intent, "UNKNOWN"))
            if isinstance(error, asyncio.CancelledError):
                raise
            return None, "SUBMIT_UNKNOWN_RECONCILE"
        state = (
            "FILLED"
            if result.filled >= request.qty - 1e-12
            else ("PARTIAL" if result.filled > 0 else "ACK")
        )
        if hasattr(self.diary, "save_order_intent_result"):
            await self.diary.save_order_intent_result(intent, state, result)
        else:
            await self.diary.save_order_intent(intent, state)
        return result, state

    async def submit(self, request):
        raise RuntimeError("USE_SUBMIT_INTENT")

    async def cancel(self, order_id, symbol):
        result = await self.inner.cancel(order_id, symbol)
        if hasattr(self.diary, "order_intents"):
            from .order_status import normalize

            for iid, meta in (await self.diary.order_intents()).items():
                if (
                    meta.get("venue") == self.venue
                    and meta.get("symbol") == symbol
                    and meta.get("order_id") == order_id
                ):
                    await self.diary.update_order_intent_reconciled(
                        iid,
                        normalize(result.status, result.filled, meta.get("qty")),
                        result,
                    )
        return result

    async def order(self, order_id, symbol):
        return await self.inner.order(order_id, symbol)

    async def order_by_client_id(self, client_order_id, symbol):
        return await self.inner.order_by_client_id(client_order_id, symbol)
