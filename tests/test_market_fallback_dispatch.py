import asyncio
import json
import time
from dataclasses import replace

import pytest
from tests.test_live_entry_dispatch import setup, op
from app.live_acceptance import (
    CHECKS,
    VENUE_CHECKS,
    MARKET_CHECKS,
    market_fallback_accepted,
)
from app.quote_order_evidence import validate


async def hybrid_setup(tmp_path):
    c, clients, positions, sent = await setup(tmp_path)
    c.market_fallback = True
    c.market_authority = lambda *args: True
    for venue, client in clients.items():
        original = client.create_order

        async def create(
            symbol, kind, side, qty, price, params, venue=venue, original=original
        ):
            if kind == "limit" and not params.get("reduceOnly"):
                sent.append((venue, kind, side, qty, price, params))
                return dict(
                    id=venue + "zero",
                    status="canceled",
                    amount=qty,
                    filled=0,
                    average=None,
                )
            return await original(symbol, kind, side, qty, price, params)

        client.create_order = create
    return c, clients, positions, sent


def test_fallback_connected_to_real_dispatch_with_four_unique_durable_proofs(tmp_path):
    async def go():
        c, clients, positions, sent = await hybrid_setup(tmp_path)
        result = await c.process([op()])
        assert result["opened"], result
        assert [x[1] for x in sent] == ["limit", "limit", "market", "market"]
        assert len({x[-1]["clientOrderId"] for x in sent}) == 4
        intents = await c.diary.order_intents()
        assert len(intents) == 4
        for iid, intent in intents.items():
            proof = await c.diary.order_request_evidence(iid)
            req = proof["request"] if "request" in proof else proof
            if "entry-market-" in iid:
                assert req["market_evidence"]["source"] == "PUBLIC_MARKET_ENTRY_V1"
                assert req["price"] is None and req["reference_price"] > 0
                assert intent["filled"] == 4
        trade = c.runtime.load()[0]
        row = await c.durable.get(trade.trade_id)
        row = json.loads(row["payload"])
        assert row["entry_stage"] == "MARKET_FALLBACK"
        assert row["fallback_native_plan"]["base_qty"] == pytest.approx(0.04)
        assert not (await c.process([op()])).get("opened", False)
        assert len(sent) == 4

    asyncio.run(go())


@pytest.mark.parametrize(
    "problem",
    [
        "authority",
        "market_authority",
        "fee",
        "margin",
        "mode",
        "funding",
        "position",
        "order",
        "stale",
        "units",
        "depth",
        "slip",
        "budget",
        "daily",
        "minimum",
    ],
)
def test_entire_admission_is_repeated_after_zero_fill(tmp_path, problem):
    async def go():
        c, clients, positions, sent = await hybrid_setup(tmp_path)
        original = c.snapshots

        async def snapshots():
            snap = await original()
            if len(sent) >= 2:
                if problem == "authority":
                    c.authority = lambda *args: False
                if problem == "market_authority":
                    c.market_authority = lambda *args: False
                if problem == "margin":
                    snap["a"]["balance"].free = 0
                if problem == "position":
                    snap["a"]["positions"] = [object()]
                if problem == "order":
                    snap["a"]["orders"] = [object()]
                if problem == "stale":
                    snap["a"]["snapshot_started_at"] -= 16
                if problem in ("fee", "mode"):

                    async def bad(symbol):
                        return (
                            dict(symbol=symbol, maker=0.05, taker=0.05)
                            if problem == "fee"
                            else dict(hedged=True)
                        )

                    if problem == "fee":
                        clients["a"].fetch_trading_fee = bad
                    else:
                        clients["a"].fetch_position_mode = bad
                if problem == "funding":

                    async def carry(*args):
                        return 0, False, "UNKNOWN"

                    c.funding.pair_carry_pct = carry
                if problem == "units":
                    clients["a"].markets[op()["symbol"]]["contractSize"] = 0.02
                if problem in ("depth", "slip", "budget"):

                    async def book(symbol, limit=None):
                        p = (
                            101
                            if problem == "slip"
                            else 150 if problem == "budget" else 100
                        )
                        return dict(
                            symbol=symbol,
                            timestamp=time.time() * 1000,
                            bids=[[p - 0.01, 1000]],
                            asks=[[p, 0.01 if problem == "depth" else 1000]],
                        )

                    clients["a"].fetch_order_book = book
                if problem == "minimum":
                    c.minimum = 0.5
                if problem == "daily":
                    import aiosqlite

                    async with aiosqlite.connect(c.diary.path) as db:
                        await db.execute(
                            "INSERT INTO live_results VALUES(?,?,?,?,?,?,?,?)",
                            ("loss", time.time(), -2, 0, 0, -2, "test", "{}"),
                        )
                        await db.commit()
            return snap

        c.snapshots = snapshots
        result = await c.process([op()])
        assert not result["opened"], result
        assert len(sent) == 2, (problem, result, sent)
        assert not c.runtime.load()

    asyncio.run(go())


@pytest.mark.parametrize("case", ["off", "uncertain", "one_fill", "both_fill"])
def test_no_blind_market_retry(tmp_path, case):
    async def go():
        c, clients, positions, sent = await hybrid_setup(tmp_path)
        if case == "off":
            c.market_fallback = False
        if case == "uncertain":

            async def unknown(*args):
                raise TimeoutError("response lost")

            clients["a"].create_order = unknown
        if case in ("one_fill", "both_fill"):
            # Replace zero-fill path with a real full-fill offline fixture.
            baseline, normal, _, _ = await setup(tmp_path / "normal")
            for venue in ("a", "b") if case == "both_fill" else ("a",):
                original = normal[venue].create_order

                async def filled(
                    symbol,
                    kind,
                    side,
                    qty,
                    price,
                    params,
                    original=original,
                    venue=venue,
                ):
                    row = await original(symbol, kind, side, qty, price, params)
                    sent.append((venue, kind, side, qty, price, params))
                    return row

                clients[venue].create_order = filled
            c.snapshots = baseline.snapshots
        await c.process([op()])
        assert not any(x[1] == "market" and not x[-1].get("reduceOnly") for x in sent)

    if case in ("one_fill", "both_fill"):
        (tmp_path / "normal").mkdir()
    asyncio.run(go())


def test_market_unknown_is_durable_and_never_resubmitted(tmp_path):
    async def go():
        c, clients, positions, sent = await hybrid_setup(tmp_path)
        original = clients["a"].create_order

        async def create(symbol, kind, side, qty, price, params):
            if kind == "market":
                sent.append(("a", kind, side, qty, price, params))
                raise TimeoutError("market acknowledgement lost")
            return await original(symbol, kind, side, qty, price, params)

        clients["a"].create_order = create
        result = await c.process([op()])
        assert "FALLBACK_UNCERTAIN" in result["status"]
        assert any(
            r["state"] == "UNKNOWN" for r in (await c.diary.order_intents()).values()
        )
        count = len(sent)
        await c.process([op()])
        assert len(sent) == count == 4
        assert not c.runtime.load()

    asyncio.run(go())


@pytest.mark.parametrize(
    "field,value",
    [
        ("minimum", float("nan")),
        ("minimum", -0.01),
        ("safety", -1),
        ("safety", True),
        ("max_seconds", 0),
        ("max_seconds", float("inf")),
        ("bankroll", True),
        ("notional", True),
    ],
)
def test_invalid_risk_configuration_cannot_send(tmp_path, field, value):
    async def go():
        c, _, _, sent = await hybrid_setup(tmp_path)
        setattr(c, field, value)
        result = await c.process([op()])
        assert not result.get("opened", False) and not sent
        assert not await c.durable.active()

    asyncio.run(go())


def test_market_authority_is_checked_again_after_durable_claim(tmp_path):
    async def go():
        c, _, _, sent = await hybrid_setup(tmp_path)
        original = c.diary.claim_order_intent

        async def claim(intent, request=None):
            result = await original(intent, request=request)
            if "entry-market-" in intent.intent_id:
                c.market_authority = lambda *args: False
            return result

        c.diary.claim_order_intent = claim
        result = await c.process([op()])
        assert not result["opened"]
        assert len(sent) == 2
        assert not c.runtime.load()

    asyncio.run(go())


def test_old_ioc_quote_does_not_block_fresh_market_quote(tmp_path):
    async def go():
        c, _, _, sent = await hybrid_setup(tmp_path)
        original = c.snapshots
        delayed = False

        async def snapshots():
            nonlocal delayed
            if len(sent) == 2 and not delayed:
                delayed = True
                await asyncio.sleep(1.6)
            return await original()

        c.snapshots = snapshots
        assert (await c.process([op()]))["opened"]
        assert delayed and len(sent) == 4

    asyncio.run(go())


@pytest.mark.parametrize("case", ["actual_fee", "actual_slippage"])
def test_actual_market_cost_breach_is_not_committed_as_open(tmp_path, case):
    async def go():
        c, clients, _, sent = await hybrid_setup(tmp_path)
        original = clients["a"].create_order

        async def create(symbol, kind, side, qty, price, params):
            row = await original(symbol, kind, side, qty, price, params)
            if kind == "market" and not params.get("reduceOnly"):
                if case == "actual_fee":
                    row["fee"]["cost"] = 1
                else:
                    row["average"] = 101
            return row

        clients["a"].create_order = create
        result = await c.process([op()])
        assert not result["opened"]
        assert (
            "ACTUAL_ENTRY_NET" in result["status"]
            if case == "actual_fee"
            else "ACTUAL_SLIPPAGE" in result["status"]
        )
        assert not c.runtime.load()
        assert await c.durable.active()

    asyncio.run(go())


def acceptance(now):
    return dict(
        version=1,
        evidence_id="offline-only",
        verified_at=now - 1,
        expires_at=now + 60,
        checks={k: True for k in CHECKS},
        venues={v: {k: True for k in VENUE_CHECKS} for v in ("a", "b")},
        hybrid=dict(venues={v: {k: True for k in MARKET_CHECKS} for v in ("a", "b")}),
    )


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing",
        "string",
        "expired",
        "future",
        "ttl",
        "venue",
        "base",
        "version",
        "id",
    ],
)
def test_market_requires_its_own_expiring_certification(tmp_path, case):
    now = time.time()
    d = acceptance(now)
    if case == "missing":
        del d["hybrid"]
    if case == "string":
        d["hybrid"]["venues"]["a"]["market_entry"] = "true"
    if case == "expired":
        d["expires_at"] = now
    if case == "future":
        d["verified_at"] = now + 1
    if case == "ttl":
        d["expires_at"] = now + 86401
    if case == "venue":
        del d["hybrid"]["venues"]["b"]
    if case == "base":
        d["checks"]["recovery"] = False
    if case == "version":
        d["version"] = True
    if case == "id":
        d["evidence_id"] = " "
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(d))
    assert market_fallback_accepted(path, ("a", "b"), now) is (case == "valid")


@pytest.mark.parametrize(
    "case",
    [
        "source",
        "reduce",
        "scope",
        "qty",
        "units",
        "stale",
        "crossed",
        "depth",
        "reference",
        "type",
        "ioc",
        "price",
    ],
)
def test_market_entry_quote_rejects_invalid_proofs(tmp_path, case):
    async def go():
        c, *_ = await setup(tmp_path)
        _, reqs, *_ = await c._prepare(op())
        r = reqs["a"]
        e = {
            **r.market_evidence,
            "source": "PUBLIC_MARKET_ENTRY_V1",
            "reduce_only": False,
        }
        r = replace(
            r,
            order_type="market",
            ioc=False,
            price=None,
            reference_price=100,
            market_evidence=e,
        )
        validate(r, "a")
        if case == "source":
            e["source"] = "PUBLIC_IOC_ENTRY_V1"
        if case == "reduce":
            e["reduce_only"] = True
        if case == "scope":
            e["venue"] = "b"
        if case == "qty":
            e["contracts"] = 5
        if case == "units":
            e["base_qty"] = 0.08
        if case == "stale":
            e["started_at"] -= 2
        if case == "crossed":
            e["bids"] = [[101, 1000]]
        if case == "depth":
            e["asks"] = [[100, 0.01]]
        if case == "reference":
            r = replace(r, reference_price=99)
        if case == "type":
            r = replace(r, order_type="limit")
        if case == "ioc":
            r = replace(r, ioc=True)
        if case == "price":
            r = replace(r, price=100)
        with pytest.raises(ValueError):
            validate(r, "a")

    asyncio.run(go())
