import asyncio
from types import SimpleNamespace as NS
import pytest
from app.private_funding_reader import Reader as FundingReader
from app.private_order_reader import Reader as OrderReader
from app.live_market_reader import Reader as MarketReader
from app.ccxt_executor import client_id
from app.runtime_state import RuntimeTrade
from app.db import Diary
from app.live_order_intent import OrderIntent
from app.exchange_executor import SubmitResult
from app.durable_order_reconcile import reconcile_and_persist

SYMBOL = "X/USDT:USDT"


class History:
    has = {"fetchFundingHistory": True}

    def __init__(self, rows):
        self.rows = rows

    async def fetch_funding_history(self, symbol, since, limit):
        return self.rows


def income(field, value, id="one", code="USDT"):
    return dict(id=id, code=code, symbol=SYMBOL, timestamp=950000, info={field: value})


@pytest.mark.parametrize(
    "venue,field,amount,expected",
    [
        ("bybit", "execFee", "2", -2),
        ("binance", "income", "-2", -2),
        ("bitget", "amount", "2", 2),
        ("bingx", "income", "2", 2),
        ("okx", "balChg", "2", 2),
    ],
)
def test_funding_uses_native_signed_cashflow(venue, field, amount, expected):
    result = asyncio.run(
        FundingReader(
            venue, History([income(field, amount)]), clock=lambda: 1100
        ).collect(SYMBOL, 900, 1000)
    )
    assert (
        result.verified and result.amount == expected and result.covered_until == 1000
    )


@pytest.mark.parametrize(
    "rows",
    [
        [income("income", 1)] * 100,
        [income("income", 1, code="BNB")],
        [income("other", 1)],
        [income("income", 1, id=None)],
        [income("income", 1), income("income", 2)],
    ],
)
def test_incomplete_funding_cannot_certify_final_net(rows):
    result = asyncio.run(
        FundingReader("binance", History(rows), clock=lambda: 1100).collect(
            SYMBOL, 900, 1000
        )
    )
    assert not result.verified


def test_funding_maturity_and_duplicate_idempotence():
    async def go():
        r = FundingReader(
            "binance", History([income("income", 1)] * 2), clock=lambda: 1000
        )
        result = await r.collect(SYMBOL, 900, 1000)
        assert not result.verified and result.amount == 1 and len(result.events) == 1
        assert result.covered_until == 970
        result = await r.collect(SYMBOL, 900, 970)
        assert result.verified and result.amount == 1
        assert not (
            await FundingReader("gateio", History([])).collect(SYMBOL, 900, 970)
        ).verified

    asyncio.run(go())


class Orders:
    async def fetch_order(self, id, symbol):
        return dict(
            id=id,
            symbol=symbol,
            status="closed",
            filled=1,
            amount=1,
            average=None,
            price=100,
        )

    async def fetch_orders(self, symbol):
        return [
            dict(
                id="1",
                symbol=symbol,
                clientOrderId=client_id("intent"),
                status="closed",
                filled=1,
                amount=1,
                average=100,
                fee=dict(currency="USDT", cost=0.1),
            )
        ]


def test_read_only_lookup_preserves_unknown_price_fee_and_stable_client_id():
    async def go():
        r = OrderReader("a", Orders())
        assert not hasattr(r, "submit") and not hasattr(r, "cancel")
        result = await r.order("1", SYMBOL)
        assert result.avg_price is None and result.fee is None
        result = await r.order_by_client_id("intent", SYMBOL)
        assert result.fee == 0.1 and result.avg_price == 100
        with pytest.raises(LookupError):
            await r.order_by_client_id("missing", SYMBOL)
        foreign = r.parse(
            dict(
                filled=1,
                status="closed",
                amount=1,
                average=100,
                fee=dict(currency="BNB", cost=0.1),
            )
        )
        assert foreign.fee is None

    asyncio.run(go())


@pytest.mark.parametrize("filled", [-1, 2, float("nan"), 0.5])
def test_reconcile_rejects_invalid_or_decreasing_cumulative_fills(tmp_path, filled):
    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        intent = OrderIntent("i", "t", "a", SYMBOL, "buy", 1, False)
        await d.save_order_intent_result(
            intent, "PARTIAL", SubmitResult("1", "open", 0.75, 100, 0.1)
        )

        class Reader:
            async def order(self, *a):
                return SubmitResult("1", "closed", filled, 100, 0.1)

        _, unresolved = await reconcile_and_persist(d, {"a": Reader()})
        assert unresolved == ("i",) and (await d.order_intents())["i"]["filled"] == 0.75

    asyncio.run(go())


class Book:
    def __init__(self, size):
        self.size = size

    def market(self, symbol):
        return dict(contractSize=self.size)

    async def fetch_order_book(self, symbol, limit):
        return dict(bids=[[102, 4]], asks=[[103, 4]], timestamp=1000000)


class Fees:
    has = {"fetchTradingFee": True}

    async def fetch_trading_fee(self, symbol):
        return dict(symbol=symbol, taker=0.001)


def position():
    return RuntimeTrade(
        "t", SYMBOL, "a", "b", 2, 4, 2, 0.5, 1, 100, 105, 900, entry_fees=0.4
    )


def test_exit_vwap_converts_contract_depth_and_requires_account_fee():
    async def go():
        r = MarketReader(
            {"a": Book(0.5), "b": Book(1)},
            {"a": Fees(), "b": Fees()},
            clock=lambda: 1000,
        )
        result = await r.mark(position())
        assert result["ok"] and result["exit_fee"] == pytest.approx(0.41)
        r.private.clear()
        r.fee_cache.clear()
        result = await r.mark(position())
        assert (
            result["ok"] and result["exit_fee"] is None and not result["fees_verified"]
        )
        r.public["a"] = Book(0.1)
        result = await r.mark(position())
        assert not result["ok"] and result["reason"] == "CONTRACT_SIZE_MISMATCH"

    asyncio.run(go())


def test_exit_book_cannot_become_stale_during_fee_lookup():
    async def go():
        now = [1000]

        class SlowFees(Fees):
            async def fetch_trading_fee(self, symbol):
                now[0] = 1003
                return await super().fetch_trading_fee(symbol)

        r = MarketReader(
            {"a": Book(0.5), "b": Book(1)},
            {"a": SlowFees(), "b": SlowFees()},
            clock=lambda: now[0],
        )
        result = await r.mark(position())
        assert not result["ok"] and "STALE" in result["reason"]

    asyncio.run(go())
