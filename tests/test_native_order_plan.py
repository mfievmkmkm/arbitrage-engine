import asyncio, copy, time
from dataclasses import replace
from types import SimpleNamespace as NS
import ccxt, pytest
from app.native_order_plan import prepare_pair, validate_request
from app.exchange_executor import SubmitRequest, SubmitResult
from app.ccxt_executor import CCXTExecutor
from app.executor_plan import build
from app.paper import PaperEngine
from app.db import Diary
from app.engine import Scanner
from app.discovery import RotatingUniverse
from app.instruments import from_market

SYMBOL = "X/USDT:USDT"


def client(size=0.01, step=1, mode=ccxt.TICK_SIZE):
    x = ccxt.Exchange()
    x.precisionMode = mode
    x.markets = {
        SYMBOL: dict(
            symbol=SYMBOL,
            base="X",
            quote="USDT",
            settle="USDT",
            contract=True,
            linear=True,
            inverse=False,
            contractSize=size,
            active=True,
            precision=dict(amount=step, price=0.01 if mode == 4 else 2),
            limits=dict(
                amount=dict(min=1, max=10000),
                cost=dict(min=1, max=1000),
                price=dict(min=0.01, max=10000),
            ),
        )
    }
    return x


def pair(a=None, b=None, qty=0.057, lp=100.003, sp=110.003):
    return prepare_pair(
        SYMBOL, "binance", "bybit", a or client(), b or client(0.001, 10), qty, lp, sp
    )


def test_common_base_step_uses_actual_ccxt_formatters_without_rounding_up():
    p = pair()
    assert p.valid and p.base_qty == 0.05
    assert p.long.qty == 5 and p.short.qty == 50 and p.long.price == 100
    assert p.long.qty * 0.01 == pytest.approx(p.short.qty * 0.001)
    assert not p.release_authorized and p.row()["evidence"].startswith("PUBLIC_NATIVE")


@pytest.mark.parametrize(
    "mode", [ccxt.TICK_SIZE, ccxt.DECIMAL_PLACES, ccxt.SIGNIFICANT_DIGITS]
)
def test_native_precision_modes_are_not_guessed(mode):
    p = pair(
        client(0.01, 1 if mode != 2 else 0, mode),
        client(0.001, 1 if mode != 2 else 0, mode),
    )
    assert (
        p.valid
        and p.base_qty <= 0.057
        and p.long.qty * 0.01 == pytest.approx(p.short.qty * 0.001)
    )


@pytest.mark.parametrize(
    "case",
    [
        "size",
        "precision",
        "mode",
        "min_unknown",
        "min_cost",
        "max_amount",
        "max_price",
        "inactive",
        "inverse",
        "identity",
        "formatter",
        "price_change",
        "dust",
        "nonfinite",
    ],
)
def test_unverified_native_parameters_block_pair(case):
    a = client()
    b = client(0.001, 10)
    m = a.markets[SYMBOL]
    qty = 0.057
    lp = 100
    if case == "size":
        m["contractSize"] = None
    if case == "precision":
        m["precision"]["amount"] = None
    if case == "mode":
        a.precisionMode = None
    if case == "min_unknown":
        m["limits"] = {}
    if case == "min_cost":
        m["limits"]["cost"]["min"] = 100
    if case == "max_amount":
        m["limits"]["amount"]["max"] = 1
    if case == "max_price":
        m["limits"]["price"]["max"] = 90
    if case == "inactive":
        m["active"] = False
    if case == "inverse":
        m["inverse"] = True
    if case == "identity":
        m["base"] = "Y"
    if case == "formatter":
        a.amount_to_precision = lambda s, q: str(float(q) + 1)
    if case == "price_change":
        m["precision"]["price"] = 10
        lp = 104
    if case == "dust":
        qty = 0.00001
    if case == "nonfinite":
        qty = float("nan")
    p = pair(a, b, qty, lp)
    assert not p.valid and not p.release_authorized


@pytest.mark.parametrize(
    "change",
    [
        dict(qty=1.1),
        dict(price=100.003),
        dict(price=None),
        dict(side="other"),
        dict(ioc="true"),
        dict(qty=float("nan")),
    ],
)
def test_request_must_already_match_native_precision(change):
    r = SubmitRequest(SYMBOL, "buy", 2, price=100)
    with pytest.raises(ValueError):
        validate_request(client(), replace(r, **change))


def test_market_requires_reference_and_has_no_limit_or_ioc_parameter():
    c = client()
    r = SubmitRequest(SYMBOL, "buy", 2, "market", reference_price=100)
    assert validate_request(c, r) == r
    for bad in (
        replace(r, reference_price=None),
        replace(r, price=100),
        replace(r, ioc=True),
    ):
        with pytest.raises(ValueError):
            validate_request(c, bad)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_legacy_plan_cannot_admit_invalid_contract_size(value):
    assert not build("X", "a", "b", 0.1, value, 0.01, lambda x: x, lambda x: x).valid


def test_legacy_rounding_cannot_expand_requested_exposure():
    assert not build(
        "X", "a", "b", 0.1, 0.01, 0.01, lambda x: x + 1, lambda x: x + 1
    ).valid


def test_ccxt_rejects_bad_native_request_before_create_and_bad_fill_after_response():
    class API:
        calls = 0

        async def create_order(self, *args):
            self.calls += 1
            raise AssertionError("must not send")

    async def go():
        c = API()
        e = CCXTExecutor("binance", c)
        with pytest.raises(ValueError):
            await e.submit(SubmitRequest(SYMBOL, "buy", 1, price=100))
        assert c.calls == 0
        good = dict(
            id="1",
            status="closed",
            amount=1,
            filled=1,
            average=100,
            fee=dict(cost=0.1, currency="USDT"),
        )
        for patch in (
            dict(filled=float("inf")),
            dict(filled=2),
            dict(average=float("nan")),
            dict(average=None, cost=100),
            dict(fee=dict(cost=float("nan"), currency="USDT")),
        ):
            with pytest.raises(RuntimeError):
                e._result(dict(good, **patch))

    asyncio.run(go())


def test_scanner_recalculates_vwap_for_native_quantity_and_paper_blocks_unknown_plan(
    tmp_path,
):
    async def go():
        a, b = client(), client(0.001, 10)

        async def book(*args, **kwargs):
            return dict(bids=[[99, 10000]], asks=[[100, 10000]])

        a.fetch_order_book = book
        b.fetch_order_book = book
        scanner = Scanner(["binance", "bybit"], 5.7, 12)
        scanner.clients = dict(binance=a, bybit=b)
        scanner.symbols = {v: {SYMBOL} for v in scanner.ids}
        scanner.specs = {
            v: {SYMBOL: from_market(v, scanner.clients[v].market(SYMBOL))}
            for v in scanner.ids
        }
        scanner.universe = RotatingUniverse(scanner.symbols)
        scanner.watch_routes = {(SYMBOL, "binance", "bybit")}
        rows = await scanner.scan()
        r = next(x for x in rows if x["buy"] == "binance")
        assert r["native_plan"]["valid"] and r["base_qty"] == 0.05
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        paper = PaperEngine(d, 500)
        assert paper.can_open(r)
        blocked = copy.deepcopy(r)
        blocked["native_plan"]["valid"] = False
        assert not paper.can_open(blocked)

    asyncio.run(go())


def test_validation_rejection_does_not_claim_intent(tmp_path):
    from app.safe_executor import SafeExecutor
    from app.live_order_intent import OrderIntent

    async def go():
        d = Diary(str(tmp_path / "d.db"))
        await d.init()
        ex = SafeExecutor("binance", CCXTExecutor("binance", NS()), d, lambda: True)
        i = OrderIntent("i", "t", "binance", SYMBOL, "buy", 1, False)
        result, reason = await ex.submit_intent(
            i, SubmitRequest(SYMBOL, "buy", 1, price=100)
        )
        assert (
            result is None
            and reason == "REQUEST_NATIVE_VALIDATION_FAILED"
            and not await d.order_intent_states()
        )

    asyncio.run(go())
