import asyncio
import copy
import time
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from app.exchange_executor import SubmitRequest
from app.quote_order_evidence import validate
from app.private_reader import PrivateReader
from app.account_health import probe
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor
from app.db import Diary
from app.live_order_intent import OrderIntent


def request():
    now = time.time()
    e = dict(
        source="PUBLIC_IOC_ENTRY_V1",
        venue="a",
        symbol="X",
        side="buy",
        contracts=10,
        contract_size=0.01,
        base_qty=0.1,
        book_ts=now,
        started_at=now,
        received_at=now,
        bids=[[99.99, 100]],
        asks=[[100, 100]],
    )
    return SubmitRequest("X", "buy", 10, "limit", 100, False, True, market_evidence=e)


@pytest.mark.parametrize(
    "problem",
    [
        "side",
        "venue",
        "amount",
        "unit",
        "future",
        "stale",
        "crossed",
        "depth",
        "limit",
        "slippage",
        "ioc",
        "type",
        "reduce",
    ],
)
def test_ioc_proof_is_scoped_fresh_and_executable(problem):
    r = request()
    e = r.market_evidence
    if problem == "side":
        e["side"] = "sell"
    if problem == "venue":
        e["venue"] = "b"
    if problem == "amount":
        e["contracts"] = 11
    if problem == "unit":
        e["base_qty"] = 1
    if problem == "future":
        e["received_at"] += 10
    if problem == "stale":
        e["book_ts"] -= 2
    if problem == "crossed":
        e["bids"] = [[101, 100]]
    if problem == "depth":
        e["asks"] = [[100, 1]]
    if problem == "limit":
        r = replace(r, price=99.9)
    if problem == "slippage":
        r = replace(r, price=101)
    if problem == "ioc":
        r = replace(r, ioc=False)
    if problem == "type":
        r = replace(r, order_type="market")
    if problem == "reduce":
        r = replace(r, reduce_only=True)
    with pytest.raises(ValueError):
        validate(r, "a")


def test_regular_intent_rechecks_gate_after_database_write(tmp_path):
    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        allow = [True]
        original = d.claim_order_intent

        async def claim(*args, **kwargs):
            result = await original(*args, **kwargs)
            allow[0] = False
            return result

        d.claim_order_intent = claim
        inner = MockExecutor()
        ex = SafeExecutor("a", inner, d, lambda: allow[0])
        result, status = await ex.submit_intent(
            OrderIntent("i", "t", "a", "X", "buy", 1, False),
            SubmitRequest("X", "buy", 1, "limit", 100, False, True),
        )
        assert (
            result is None
            and status == "RECOVERY_PRE_SEND_GUARD_FAILED"
            and inner.seq == 0
        )
        assert (await d.order_intent_states())["i"] == "FAILED"

    asyncio.run(go())


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1])
def test_invalid_private_contracts_are_unknown_not_a_flat_account(value):
    class C:
        def market(self, symbol):
            return {"contractSize": 1}

        async def fetch_positions(self):
            return [{"symbol": "X", "contracts": value}]

        async def fetch_open_orders(self):
            return []

        async def fetch_balance(self):
            return {"USDT": {"free": 50, "used": 0, "total": 50}}

    health, *_ = asyncio.run(probe(PrivateReader("a", C())))
    assert not health.ok
