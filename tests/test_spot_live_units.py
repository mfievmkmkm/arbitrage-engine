import asyncio
import copy
import time
from dataclasses import replace
from types import SimpleNamespace as NS
import ccxt
import pytest
from app.exchange_executor import SubmitRequest, SubmitResult
from app.spot_native_order import validate_request
from app.spot_future_native_plan import prepare, hedge, spot_close
from app.spot_executor import parse, SpotExecutor, SpotOrderReader
from app.spot_quote_evidence import validate
from app.spot_account_reader import Reader, Snapshot
from app.spot_future_cashflow import rebuild, verify_private
from app.db import Diary
from app.live_order_intent import OrderIntent
from app.durable_order_reconcile import reconcile_and_persist

SS, FS = "X/USDT", "X/USDT:USDT"


def client(mode=ccxt.TICK_SIZE):
    c = ccxt.Exchange()
    c.precisionMode = mode
    precision = dict(amount=0.00001 if mode == 4 else 5, price=0.01 if mode == 4 else 2)
    common = dict(
        base="X",
        quote="USDT",
        active=True,
        precision=precision,
        limits=dict(amount=dict(min=0.00001, max=1000), cost=dict(min=1, max=10000)),
    )
    c.markets = {
        SS: dict(common, symbol=SS, spot=True, contract=False, settle=None),
        FS: dict(
            common,
            symbol=FS,
            spot=False,
            contract=True,
            linear=True,
            inverse=False,
            settle="USDT",
            contractSize=0.001,
            precision=dict(
                amount=1 if mode == 4 else (3 if mode == 3 else 0),
                price=precision["price"],
            ),
        ),
    }
    return c


def request():
    now = time.time()
    e = dict(
        source="PUBLIC_SPOT_IOC_V1",
        market_type="spot",
        venue="binance:spot",
        symbol=SS,
        side="buy",
        contracts=0.04,
        contract_size=1,
        base_qty=0.04,
        book_ts=now,
        started_at=now,
        received_at=now,
        bids=[[99.99, 1]],
        asks=[[100, 1]],
    )
    return SubmitRequest(SS, "buy", 0.04, price=100, ioc=True, market_evidence=e)


def raw(currency="X", fee=0.00004, filled=0.04):
    return dict(
        id="o",
        symbol=SS,
        amount=0.04,
        filled=filled,
        average=100 if filled else None,
        status="canceled",
        fee=dict(currency=currency, cost=fee),
    )


def rows():
    def row(i, venue, symbol, side, qty, price, reduce=False, bf=0, currency=None):
        return dict(
            intent_id=i,
            trade_id="t",
            venue=venue,
            symbol=symbol,
            side=side,
            qty=qty,
            filled=qty,
            avg_price=price,
            fee=0.001,
            reduce_only=reduce,
            base_fee=bf,
            base_currency=currency,
            state="FILLED",
            order_id=i,
            _journal_sequence=int(i),
        )

    return {
        "1": row("1", "binance:spot", SS, "buy", 0.05, 100, bf=0.00005, currency="X"),
        "2": row("2", "binance", FS, "sell", 49, 110),
        "3": row("3", "binance", FS, "buy", 49, 100, True),
        "4": row(
            "4", "binance:spot", SS, "sell", 0.0499, 100, bf=0.0000499, currency="X"
        ),
    }


def flow(values=None):
    return rebuild(
        values if values is not None else rows(),
        "t",
        "binance:spot",
        "binance",
        SS,
        FS,
        "X",
        0.001,
    )


@pytest.mark.parametrize(
    "mode", [ccxt.TICK_SIZE, ccxt.DECIMAL_PLACES, ccxt.SIGNIFICANT_DIGITS]
)
def test_spot_native_fee_reserve_and_close_are_never_rounded_up(mode):
    c = client(mode)
    p = prepare(c, c, SS, FS, 0.05, 100, 110, 0.001, max_dust_usd=1)
    assert p.valid, p.reason
    assert p.hedged_base <= p.expected_spot_credit < p.spot.qty
    assert p.future.qty * 0.001 == pytest.approx(p.hedged_base)
    assert not p.release_authorized
    exit = spot_close(c, SS, p.expected_spot_credit, 100, 0.001)
    assert exit.qty * 1.001 <= p.expected_spot_credit + 1e-15
    validate_request(c, exit)


@pytest.mark.parametrize(
    "field,value",
    [
        ("spot", False),
        ("contract", True),
        ("quote", "USD"),
        ("active", None),
        ("settle", "USDT"),
    ],
)
def test_unknown_or_non_cash_market_is_blocked(field, value):
    c = client()
    c.markets[SS][field] = value
    with pytest.raises(ValueError):
        validate_request(c, request())


def test_fee_reserve_can_make_an_apparent_minimum_unhedgeable():
    c = client()
    c.markets[FS]["limits"]["cost"]["min"] = 5.5
    p = prepare(c, c, SS, FS, 0.05, 100, 110, 0.001)
    assert not p.valid and p.reason == "NATIVE_COST_MIN"


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf"), -0.1, 0.11])
def test_fee_rates_are_strict(value):
    assert not prepare(client(), client(), SS, FS, 0.05, 100, 110, value).valid


def test_partial_spot_recomputes_native_future_amount_from_net_credit():
    r = parse(client(), raw(fee=0.00002, filled=0.02))
    h, base = hedge(client(), FS, r.filled - r.base_fee, 110)
    assert h.qty == 19 and base == 0.019


def test_base_and_quote_fees_have_distinct_cash_units():
    r = raw()
    r["fee"] = None
    r["fees"] = [dict(currency="X", cost=0.00004), dict(currency="USDT", cost=0.002)]
    parsed = parse(client(), r)
    assert (
        parsed.base_currency == "X"
        and parsed.base_fee == 0.00004
        and parsed.fee == 0.002
    )
    assert SpotOrderReader("binance:spot", client()).parse(r) == parsed


@pytest.mark.parametrize("currency", ["BNB", "USD", "", "BTC"])
def test_third_currency_fee_needs_verified_conversion(currency):
    with pytest.raises(ValueError, match="CONVERSION"):
        parse(client(), raw(currency, 0.001))


@pytest.mark.parametrize(
    "field,value",
    [
        ("filled", True),
        ("amount", True),
        ("filled", None),
        ("amount", None),
        ("id", None),
        ("average", float("nan")),
    ],
)
def test_invalid_actual_orders_never_become_owned_inventory(field, value):
    r = raw()
    r[field] = value
    with pytest.raises((ValueError, RuntimeError)):
        parse(client(), r)


def test_missing_fee_and_fee_exceeding_fill_are_unknown():
    r = raw()
    r["fee"] = None
    with pytest.raises(RuntimeError, match="FEE_UNKNOWN"):
        parse(client(), r)
    with pytest.raises(ValueError, match="EXCEEDS_FILL"):
        parse(client(), raw(fee=0.1))


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_size", 0.001),
        ("market_type", "future"),
        ("venue", "binance"),
        ("base_qty", 0.01),
        ("book_ts", 1),
    ],
)
def test_cash_proof_units_scope_and_time_are_checked(field, value):
    r = request()
    e = dict(r.market_evidence, **{field: value})
    with pytest.raises(ValueError):
        validate(replace(r, market_evidence=e), "binance:spot")


def test_cash_quote_cannot_validate_derivative_adapter():
    from app.ccxt_executor import CCXTExecutor

    with pytest.raises(ValueError, match="DERIVATIVE_SPOT"):
        CCXTExecutor("binance:spot", client()).validate(request())


def test_cashflow_replays_in_sqlite_order_and_dust_is_not_profit_or_flat():
    original = rows()
    reversed_rows = dict(reversed(list(original.items())))
    f = flow(reversed_rows)
    assert f == flow(original)
    assert f.spot_base == pytest.approx(0.0000001)
    assert f.future_base == 0
    assert f.future_realized == pytest.approx(0.49)
    assert f.net(0.01, True) == pytest.approx(-5 + 4.99 + 0.49 - 0.004 + 0.01)
    assert f.spot_cost_basis > 0
    assert f.base_fees == pytest.approx(0.0000999)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda r: r["2"].update(qty=50, filled=50), "HEDGE_EXCEEDS"),
        (lambda r: r["4"].update(qty=0.05, filled=0.05), "SELL_EXCEEDS"),
        (lambda r: r["2"].update(state="UNKNOWN"), "UNRESOLVED"),
        (lambda r: r["1"].update(base_currency=None), "UNITS_UNKNOWN"),
        (lambda r: r["1"].update(_journal_sequence=None), "SEQUENCE_UNKNOWN"),
        (lambda r: r["1"].update(trade_id="other"), "IDENTITY_MISMATCH"),
        (lambda r: r["4"].update(venue="other"), "SCOPE_MISMATCH"),
    ],
)
def test_cashflow_never_invents_a_hedge_or_uses_preexisting_inventory(mutation, reason):
    r = rows()
    mutation(r)
    with pytest.raises(ValueError, match=reason):
        flow(r)


def test_open_future_or_unverified_funding_has_no_final_net():
    r = rows()
    del r["3"]
    del r["4"]
    with pytest.raises(ValueError):
        flow(r).net(0, True)
    with pytest.raises(ValueError):
        flow().net(0, False)


def test_private_balance_is_baseline_plus_owned_asset_not_gross_order_fill():
    f = flow()
    now = time.time()
    s = Snapshot(
        "binance:spot",
        {"X": dict(total=2 + f.spot_base, free=2 + f.spot_base)},
        (),
        now,
        now,
    )
    proof = verify_private(f, 2, s, "X", [], FS, 0.001, now)
    assert proof["future_flat"] and not proof["spot_flat"]
    bad = replace(s, balances={"X": dict(total=2, free=2)})
    with pytest.raises(ValueError, match="SPOT_MISMATCH"):
        verify_private(f, 2, bad, "X", [], FS, 0.001, now)


@pytest.mark.parametrize("value", [None, True, float("nan"), -1])
def test_spot_account_reader_does_not_default_missing_or_invalid_balances_to_zero(
    value,
):
    async def go():
        c = NS()

        async def balance():
            return {
                "X": dict(free=value, used=0, total=value),
                "USDT": dict(free=50, used=0, total=50),
            }

        async def orders():
            return []

        c.fetch_balance, c.fetch_open_orders = balance, orders
        with pytest.raises(ValueError):
            await Reader("binance:spot", c).snapshot(["X"])

    asyncio.run(go())


def test_base_fees_survive_sqlite_restart_and_conflicting_late_lookup_is_held(tmp_path):
    async def go():
        d = Diary(str(tmp_path / "d.sqlite"))
        await d.init()
        i = OrderIntent("i", "t", "binance:spot", SS, "buy", 0.04, False)
        r = parse(client(), raw())
        await d.save_order_intent_result(i, "CANCELED", r)
        restored = Diary(d.path)
        meta = (await restored.order_intents())["i"]
        assert meta["base_fee"] == r.base_fee and meta["base_currency"] == "X"
        c = NS()

        async def order(*args):
            return replace(r, base_fee=0)

        c.order = order
        await restored.update_order_intent_reconciled("i", "UNKNOWN")
        states, unresolved = await reconcile_and_persist(restored, {"binance:spot": c})
        assert states["i"] == "UNKNOWN" and unresolved == ("i",)
        assert (await restored.order_intents())["i"]["base_fee"] == r.base_fee

    asyncio.run(go())


def test_private_balance_cannot_prove_a_tiny_credit_below_float_resolution():
    from app.spot_future_cashflow import Cashflow

    now = time.time()
    f = Cashflow(1e-17, 0, -1e-15, 0, 0, 0, 1e-15)
    s = Snapshot("binance:spot", {"X": dict(total=2, free=2)}, (), now, now)
    with pytest.raises(ValueError, match="RESOLUTION_INSUFFICIENT"):
        verify_private(f, 2, s, "X", [], FS, 0.001, now)


def test_locked_owned_inventory_cannot_be_sold_even_if_total_matches():
    f, now = flow(), time.time()
    s = Snapshot("binance:spot", {"X": dict(total=f.spot_base, free=0)}, (), now, now)
    with pytest.raises(ValueError, match="NOT_FREE"):
        verify_private(f, 0, s, "X", [], FS, 0.001, now)


def test_base_fee_usd_attribution_does_not_get_subtracted_twice():
    f = flow()
    assert f.base_fee_usd == pytest.approx(f.base_fees * 100)
    gross = f.spot_cash + f.future_realized + f.base_fee_usd
    fees = f.quote_fees + f.base_fee_usd
    assert gross - fees == pytest.approx(f.net(0, True))


def test_spot_future_market_detail_uses_base_and_native_contract_units():
    from app.tg_market_detail import render

    p = prepare(client(), client(), SS, FS, 0.04545, 100, 110, 0.001)
    assert p.valid
    text = render(
        "spot_futures",
        dict(
            base="X",
            exchange="binance",
            direction="LONG_SPOT_SHORT_FUTURE",
            native_plan=p.row(),
        ),
    )
    assert "Спот gross" in text and "Незахеджированный остаток" in text
    assert "BASE" in text and "контрактов" in text


def test_borrowing_unverified_detail_never_displays_an_executable_plan():
    from app.tg_market_detail import render

    text = render(
        "spot_futures",
        dict(
            base="X", native_plan=dict(valid=False, reason="SPOT_BORROWING_UNVERIFIED")
        ),
    )
    assert "SPOT_BORROWING_UNVERIFIED" in text and "План заявок заблокирован" in text


def test_cash_inventory_is_in_full_audit_export_manifest():
    from app.audit_export import TABLES

    assert TABLES["Live cash inventory"] == "live_cash_inventory"
