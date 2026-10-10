import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS
import json
import csv, io, zipfile
import ccxt
import pytest
from app import recovery_market as rm
from app.exchange_executor import SubmitRequest, SubmitResult
from app.db import Diary
from app.live_order_intent import OrderIntent
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor
from app.ccxt_executor import CCXTExecutor
from app.recovery_flow import recover
from app.close_recovery_executor import recover_close
from app.private_close_recovery import recover_from_private
from app.persisted_exit_runner import run as protective_exit
from app.runtime_state import RuntimeTrade
from app.exit_runner import ExitResult
from app.private_adapter import PrivatePosition
from app.audit_export import build as export

SYMBOL = "X/USDT:USDT"


class Client(ccxt.Exchange):
    def __init__(self, book=None, size=0.1):
        super().__init__()
        self.precisionMode = ccxt.TICK_SIZE
        self.set_markets(
            [
                dict(
                    id="X",
                    symbol=SYMBOL,
                    base="X",
                    quote="USDT",
                    settle="USDT",
                    spot=False,
                    swap=True,
                    future=False,
                    contract=True,
                    linear=True,
                    inverse=False,
                    active=True,
                    contractSize=size,
                    precision=dict(amount=1, price=0.1),
                    limits=dict(
                        amount=dict(min=1, max=100), cost=dict(min=1), price=dict(min=1)
                    ),
                )
            ]
        )
        self.book = (
            book
            if book is not None
            else dict(
                symbol=SYMBOL,
                timestamp=1000000,
                bids=[[100, 10], [99.9, 10]],
                asks=[[100.1, 10], [100.2, 10]],
            )
        )
        self.calls = []

    async def fetch_order_book(self, symbol, limit):
        return deepcopy(self.book)

    async def create_order(self, symbol, type, side, qty, price, params):
        self.calls.append((symbol, type, side, qty, price, params))
        return dict(
            id="r",
            status="closed",
            filled=qty,
            amount=qty,
            average=100.1 if side == "buy" else 100,
            fee=dict(currency="USDT", cost=0.01),
        )


def request(side="buy", qty=12):
    return SubmitRequest(SYMBOL, side, qty, "market", None, True)


def test_native_quote_uses_contract_depth_and_vwap():
    async def go():
        c = Client()
        r = await rm.Reader({"a": c}, clock=lambda: 1000).quote("a", request(), 0.1)
        assert r.price is None and r.reference_price == pytest.approx(
            (10 * 100.1 + 2 * 100.2) / 12
        )
        assert r.market_evidence["base_qty"] == pytest.approx(1.2)
        assert rm.validate_evidence(r, "a", 1000)

    asyncio.run(go())


@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp", 998000),
        ("timestamp", 1000001),
        ("timestamp", float("nan")),
        ("timestamp", True),
        ("symbol", "Y/USDT:USDT"),
        ("bids", []),
        ("asks", []),
        ("bids", [[100, 10], [100.1, 10]]),
        ("asks", [[100.1, 10], [100, 10]]),
        ("asks", [[100.1, 10], [100.1, 10]]),
        ("asks", [[100.1, -1]]),
        ("asks", [[100.1, float("nan")]]),
        ("asks", [[True, 10]]),
        ("asks", [[100.1, 10]]),
        ("bids", [[100.1, 20]]),
        ("asks", [[100.1, 10], [101, 20]]),
    ],
)
def test_bad_book_blocks_before_order(field, value):
    async def go():
        c = Client()
        c.book[field] = value
        with pytest.raises(ValueError):
            await rm.Reader({"a": c}, clock=lambda: 1000).quote("a", request(), 0.1)
        assert not c.calls

    asyncio.run(go())


@pytest.mark.parametrize(
    "qty,size", [(0.5, 0.1), (101, 0.1), (12, 0.2), (float("inf"), 0.1)]
)
def test_native_quantity_and_size_checks(qty, size):
    async def go():
        with pytest.raises(ValueError):
            await rm.Reader({"a": Client()}, clock=lambda: 1000).quote(
                "a", request(qty=qty), size
            )

    asyncio.run(go())


def test_no_exchange_timestamp_uses_start_and_blocks_slow_fetch():
    async def go():
        now = [1000]
        c = Client()
        c.book.pop("timestamp")
        reader = rm.Reader({"a": c}, clock=lambda: now[0])
        r = await reader.quote("a", request(), 0.1)
        assert r.market_evidence["book_ts"] == 1000

        async def slow(symbol, limit):
            now[0] += 2
            return deepcopy(c.book)

        c.fetch_order_book = slow
        with pytest.raises(ValueError, match="STALE"):
            await reader.quote("a", request(), 0.1)

    asyncio.run(go())


@pytest.mark.parametrize(
    "field,value",
    [
        ("venue", "b"),
        ("symbol", "Y"),
        ("side", "sell"),
        ("contracts", 11),
        ("reduce_only", 1),
        ("base_qty", 2),
        ("source", "OTHER"),
        ("received_at", 1001),
    ],
)
def test_quote_is_bound_to_request(field, value):
    async def go():
        r = await rm.Reader({"a": Client()}, clock=lambda: 1000).quote(
            "a", request(), 0.1
        )
        r.market_evidence[field] = value
        with pytest.raises(ValueError):
            rm.validate_evidence(r, "a", 1000)

    asyncio.run(go())


def intent(r):
    return OrderIntent(
        "t:close-recovery:a:sell", "t", "a", r.symbol, r.side, r.qty, r.reduce_only
    )


def fake_clock(monkeypatch, now):
    monkeypatch.setattr(rm, "time", NS(time=lambda: now[0]))


def test_safe_executor_persists_evidence_before_native_submit_and_restart(
    tmp_path, monkeypatch
):
    now = [1000]
    fake_clock(monkeypatch, now)

    async def go():
        c = Client()
        d = Diary(str(tmp_path / "db"))
        await d.init()
        r = await rm.Reader({"a": c}, clock=lambda: now[0]).quote(
            "a", request("sell", 2), 0.1
        )
        r = replace(r, client_order_id=intent(r).intent_id)
        original = c.create_order

        async def captured(*args):
            proof = await d.order_request_evidence(intent(r).intent_id)
            assert proof["market_evidence"]["bids"] == c.book["bids"]
            assert (await d.order_intent_states("t"))[
                intent(r).intent_id
            ] == "SUBMITTING"
            return await original(*args)

        c.create_order = captured
        ex = SafeExecutor("a", CCXTExecutor("a", c), d, lambda: True)
        result, state = await ex.submit_intent(intent(r), r)
        assert result.filled == 2 and state == "FILLED"
        restarted = SafeExecutor("a", CCXTExecutor("a", c), Diary(d.path), lambda: True)
        assert (await restarted.submit_intent(intent(r), r))[
            1
        ] == "DUPLICATE_OR_UNRESOLVED_INTENT"
        assert len(c.calls) == 1
        _, archive = await export(d.path, tmp_path / "out")
        with zipfile.ZipFile(archive) as z:
            rows = list(
                csv.DictReader(
                    io.StringIO(
                        z.read("order_request_evidence.csv").decode("utf-8-sig")
                    )
                )
            )
        assert json.loads(rows[0]["payload"])["reference_price"] == 100

    asyncio.run(go())


def test_database_delay_invalidates_quote_before_network(tmp_path, monkeypatch):
    now = [1000]
    fake_clock(monkeypatch, now)

    class SlowDiary(Diary):
        async def claim_order_intent(self, *args, **kwargs):
            result = await super().claim_order_intent(*args, **kwargs)
            now[0] += 2
            return result

    async def go():
        c = Client()
        d = SlowDiary(str(tmp_path / "db"))
        await d.init()
        r = await rm.Reader({"a": c}, clock=lambda: now[0]).quote("a", request(), 0.1)
        ex = SafeExecutor("a", CCXTExecutor("a", c), d, lambda: True)
        assert (await ex.submit_intent(intent(r), r))[
            1
        ] == "RECOVERY_PRE_SEND_GUARD_FAILED"
        assert (
            not c.calls
            and (await d.order_intent_states("t"))[intent(r).intent_id] == "FAILED"
        )
        assert await d.order_request_evidence(intent(r).intent_id)

    asyncio.run(go())


def test_failed_evidence_serialization_rolls_back_intent(tmp_path):
    async def go():
        d = Diary(str(tmp_path / "db"))
        await d.init()
        r = request()
        r = replace(r, reference_price=float("nan"))
        with pytest.raises(ValueError):
            await d.claim_order_intent(intent(r), r)
        assert not await d.order_intent_states("t")
        assert await d.order_request_evidence(intent(r).intent_id) is None

    asyncio.run(go())


def trade():
    return RuntimeTrade("t", SYMBOL, "a", "b", 0.2, 2, 2, 0.1, 0.1, 100, 110, 0)


def test_entry_recovery_uses_fresh_reference_and_flags_actual_slippage():
    class Capture(MockExecutor):
        async def submit(self, r):
            assert r.market_evidence and r.reference_price == 100 and r.price is None
            return await super().submit(r)

    async def go():
        reader = rm.Reader({"a": Client()}, clock=lambda: 1000)
        x = await recover(
            SYMBOL,
            "a",
            "b",
            0.2,
            0,
            0.1,
            0.1,
            Capture(price=98),
            MockExecutor(),
            0,
            0,
            2,
            market_reader=reader,
        )
        assert not x.completed and x.error == "RECOVERY_ACTUAL_SLIPPAGE_STOP"
        assert x.result.filled == 2

    asyncio.run(go())


def test_partial_recovery_preserves_native_proof_and_accounting():
    async def go():
        t = trade()
        x = ExitResult(
            SubmitResult("l", "canceled", 1, 100, 0.1),
            SubmitResult("s", "closed", 2, 110, 0.2),
            False,
            0,
        )
        reader = rm.Reader({"a": Client()}, clock=lambda: 1000)
        result = await recover_close(
            t, x, MockExecutor(0.5, 100), MockExecutor(), market_reader=reader
        )
        assert not result.recovered and result.execution.long_result.filled == 1.5
        assert result.execution.long_result.fee == 0.1

    asyncio.run(go())


def test_private_quote_failure_prevents_both_submissions():
    async def go():
        a, b = MockExecutor(), MockExecutor()
        snapshot = {
            v: dict(
                health=NS(ok=True),
                positions=[
                    PrivatePosition(
                        v, SYMBOL, side, 0.2, contracts=2, contract_size=0.1
                    )
                ],
            )
            for v, side in (("a", "long"), ("b", "short"))
        }
        bad = Client()
        bad.book["timestamp"] = 900000
        x = await recover_from_private(
            trade(),
            snapshot,
            a,
            b,
            market_reader=rm.Reader({"a": Client(), "b": bad}, clock=lambda: 1000),
        )
        assert not x.recovered and "QUOTE_BLOCKED" in x.reason
        assert a.seq == b.seq == 0

    asyncio.run(go())


def test_protective_exit_checks_both_books_before_submit():
    async def go():
        a, b = MockExecutor(), MockExecutor()
        x = await protective_exit(
            trade(), a, b, market_reader=rm.Reader({"a": Client()}, clock=lambda: 1000)
        )
        assert x.execution is None and "QUOTE_BLOCKED" in x.status
        assert a.seq == b.seq == 0

    asyncio.run(go())


def test_complete_cannot_add_exposure_using_old_profit_estimate():
    async def go():
        a, b = MockExecutor(), MockExecutor()
        x = await recover(
            SYMBOL,
            "a",
            "b",
            0.2,
            0,
            0.1,
            0.1,
            a,
            b,
            1,
            0,
            2,
            market_reader=rm.Reader({"a": Client(), "b": Client()}, clock=lambda: 1000),
        )
        assert not x.completed and x.error == "RECOVERY_COMPLETE_FRESH_NET_REQUIRED"
        assert a.seq == b.seq == 0

    asyncio.run(go())


def test_gate_closing_while_persisting_prevents_order(tmp_path, monkeypatch):
    now = [1000]
    fake_clock(monkeypatch, now)
    allowed = [True]

    class GateDiary(Diary):
        async def claim_order_intent(self, *args, **kwargs):
            r = await super().claim_order_intent(*args, **kwargs)
            allowed[0] = False
            return r

    async def go():
        c = Client()
        d = GateDiary(str(tmp_path / "db"))
        await d.init()
        r = await rm.Reader({"a": c}, clock=lambda: 1000).quote("a", request(), 0.1)
        ex = SafeExecutor("a", CCXTExecutor("a", c), d, lambda: allowed[0])
        assert (await ex.submit_intent(intent(r), r))[
            1
        ] == "RECOVERY_PRE_SEND_GUARD_FAILED"
        assert not c.calls

    asyncio.run(go())


def test_unknown_submit_keeps_original_quote_and_never_retries(tmp_path, monkeypatch):
    now = [1000]
    fake_clock(monkeypatch, now)

    async def go():
        c = Client()
        d = Diary(str(tmp_path / "db"))
        await d.init()
        r = await rm.Reader({"a": c}, clock=lambda: 1000).quote("a", request(), 0.1)
        calls = []

        async def timeout(*args):
            calls.append(args)
            raise TimeoutError()

        c.create_order = timeout
        ex = SafeExecutor("a", CCXTExecutor("a", c), d, lambda: True)
        assert (await ex.submit_intent(intent(r), r))[1] == "SUBMIT_UNKNOWN_RECONCILE"
        proof = await d.order_request_evidence(intent(r).intent_id)
        assert (await ex.submit_intent(intent(r), r))[
            1
        ] == "DUPLICATE_OR_UNRESOLVED_INTENT"
        assert await d.order_request_evidence(intent(r).intent_id) == proof
        assert len(calls) == 1

    asyncio.run(go())


def test_cancel_conflict_does_not_overwrite_known_fills(tmp_path):
    class Conflicting(MockExecutor):
        async def cancel(self, oid, symbol):
            return SubmitResult(oid, "canceled", 0.5, 100, 0)

    async def go():
        d = Diary(str(tmp_path / "db"))
        await d.init()
        r = SubmitRequest(SYMBOL, "sell", 2, "limit", 100, True)
        i = intent(r)
        await d.save_order_intent_result(
            i, "PARTIAL", SubmitResult("r", "open", 1, 100, 0.1)
        )
        ex = SafeExecutor("a", Conflicting(), d, lambda: True)
        with pytest.raises(RuntimeError, match="CONFLICT"):
            await ex.cancel("r", SYMBOL)
        meta = (await d.order_intents())[i.intent_id]
        assert meta["state"] == "UNKNOWN" and meta["filled"] == 1 and meta["fee"] == 0.1

    asyncio.run(go())


def test_protective_exit_retains_fills_when_actual_price_exceeds_guard(
    tmp_path, monkeypatch
):
    now = [1000]
    fake_clock(monkeypatch, now)

    async def go():
        d = Diary(str(tmp_path / "db"))
        await d.init()
        a = SafeExecutor("a", MockExecutor(price=98), d, lambda: True)
        b = SafeExecutor("b", MockExecutor(price=100.1), d, lambda: True)
        x = await protective_exit(
            trade(),
            a,
            b,
            market_reader=rm.Reader({"a": Client(), "b": Client()}, clock=lambda: 1000),
        )
        assert x.status == "EXIT_ACTUAL_SLIPPAGE_STOP" and x.execution.flat
        assert x.execution.long_result.avg_price == 98
        assert len(await d.order_intent_states("t")) == 2

    asyncio.run(go())


def test_depth_does_not_round_missing_dust_into_full_liquidity():
    with pytest.raises(ValueError, match="LIQUIDITY"):
        rm.executable([[100, 1e-16]], 1e-15)


def test_reconcile_cannot_regress_fills_inside_database_transaction(tmp_path):
    async def go():
        d = Diary(str(tmp_path / "db"))
        await d.init()
        r = SubmitRequest(SYMBOL, "sell", 2, "limit", 100, True)
        i = intent(r)
        await d.save_order_intent_result(
            i, "PARTIAL", SubmitResult("r", "open", 1, 100, 0.1)
        )
        assert await d.update_order_intent_reconciled(
            i.intent_id, "PARTIAL", SubmitResult("r", "open", 1.5, 101, 0.2)
        )
        assert not await d.update_order_intent_reconciled(
            i.intent_id, "CANCELED", SubmitResult("r", "canceled", 1.2, 100, 0.15)
        )
        meta = (await d.order_intents())[i.intent_id]
        assert (
            meta["state"] == "UNKNOWN" and meta["filled"] == 1.5 and meta["fee"] == 0.2
        )

    asyncio.run(go())


def test_exit_slippage_sets_durable_hold_in_session(monkeypatch):
    from app import live_session
    from app.live_trade_lifecycle import LifecycleClose

    class Durable:
        def __init__(self):
            self.phases = []

        async def get(self, tid):
            return {"phase": "OPEN"}

        async def phase(self, tid, phase, **kwargs):
            self.phases.append((phase, kwargs))

    async def fake(*args, **kwargs):
        return LifecycleClose(False, "EXIT_ACTUAL_SLIPPAGE_STOP")

    monkeypatch.setattr(live_session, "close_execute", fake)

    async def go():
        durable = Durable()
        result = await live_session.close_trade(
            None, None, [], trade(), None, None, None, durable=durable
        )
        assert not result.ok
        assert durable.phases[-1] == (
            "UNKNOWN",
            {"exit_hold_reason": "EXIT_ACTUAL_SLIPPAGE_STOP"},
        )

    asyncio.run(go())


def test_quote_reader_cannot_change_requested_exposure():
    class WrongReader(rm.Reader):
        async def quote(self, venue, r, size):
            return await super().quote(venue, replace(r, qty=1), size)

    async def go():
        reader = WrongReader({"a": Client()}, clock=lambda: 1000)
        with pytest.raises(ValueError, match="CHANGED_REQUEST"):
            await rm.prepare(reader, "a", request(), 0.1)

    asyncio.run(go())
