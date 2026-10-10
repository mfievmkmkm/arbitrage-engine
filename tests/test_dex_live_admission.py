import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import pytest
from app.dex_live_admission import Admission
from app.dex_live_bridge import Plan
from app.dex_live_costs import USDT_MAINNET
from app.dex_firm_simulation import Envelope
from app.live_acceptance import CHECKS, VENUE_CHECKS
from tests.test_native_order_plan import client, SYMBOL
from tests.test_dex_live_bridge import NOW, ASSET, WALLET
from tests.test_dex_live_costs import Gas


async def setup(tmp_path, fault):
    from app.live_monitor_store import Store

    path = str(tmp_path / "db.sqlite")
    await Store(path).init()
    c = client(size="0.001")
    c.has.update(fetchTradingFee=True, fetchPositionMode=True)

    async def fee(symbol):
        return dict(symbol=symbol, taker="0.001")

    async def positions():
        return []

    async def orders():
        return []

    async def mode(symbol):
        return dict(hedged=fault == "hedged")

    async def balance():
        return dict(USDT=dict(free=1 if fault == "margin" else 50, used=0, total=50))

    c.fetch_trading_fee, c.fetch_positions, c.fetch_open_orders = fee, positions, orders
    c.fetch_position_mode, c.fetch_balance = mode, balance

    async def prepare(p, raw, closing):
        return NS(
            qty=str(abs(Decimal(raw)) / 10**18 / Decimal("0.001")),
            price="400" if fault == "negative_edge" else "410",
            reduce_only=False,
            side="sell",
            market_evidence=dict(book_ts=NOW - (2 if fault == "stale_cex" else 0)),
        )

    async def funding(venue, symbol):
        return NS(
            exchange=venue,
            symbol=symbol,
            rate="0.00001",
            next_ts=None if fault == "funding" else (NOW + 1000) * 1000,
            interval_hours=8,
        )

    async def reverse(p, raw):
        return Envelope(
            dict(
                sell_token=ASSET,
                buy_token=USDT_MAINNET,
                requested_amount_raw=str(raw),
                quote_mode="exact_in",
                network_fee_raw=str(10**12),
            ),
            {},
            WALLET,
        )

    registry = dict(
        version=1,
        evidence_id="OFFLINE_TEST_NOT_CERTIFICATION",
        verified_at=NOW - 10,
        expires_at=NOW + 100,
        chains={
            "1": dict(
                network_verified=True,
                transaction_targets=["0x" + "4" * 40],
                tokens={
                    ASSET: dict(
                        contract_verified=True,
                        decimals=18,
                        base="X",
                        cex_venue="a",
                        cex_symbol=SYMBOL,
                    ),
                    USDT_MAINNET: dict(
                        contract_verified=True, decimals=6, quote_usdt=True
                    ),
                },
            )
        },
    )
    registry_path, acceptance_path = (
        tmp_path / "registry.json",
        tmp_path / "acceptance.json",
    )
    registry_path.write_text(json.dumps(registry))
    acceptance_path.write_text(
        json.dumps(
            dict(
                version=1,
                evidence_id="OFFLINE_TEST_NOT_CERTIFICATION",
                verified_at=NOW - 10,
                expires_at=NOW + 100,
                checks={k: True for k in CHECKS},
                venues={"a": {k: True for k in VENUE_CHECKS}},
            )
        )
    )
    a = Admission(
        path,
        NS(clients={"a": c}, prepare=prepare),
        NS(check=lambda *args: None),
        str(registry_path),
        str(acceptance_path),
        Gas(),
        NS(get=funding),
        reverse,
        clock=lambda: NOW,
    )
    p = Plan(
        "a",
        SYMBOL,
        WALLET,
        ASSET,
        USDT_MAINNET,
        18,
        6,
        "0.001",
        "forward",
        NOW,
        "5",
        "0.005",
    )
    q = Envelope(
        dict(
            buy_amount_raw=str(10**16),
            max_sell_amount_raw="4000000",
            network_fee_raw=str(10**12),
        ),
        {},
        WALLET,
    )
    if fault == "identity":
        p = replace(p, asset_decimals=6)
    if fault == "daily_loss":
        import aiosqlite

        async with aiosqlite.connect(path) as d:
            await d.execute(
                "INSERT INTO live_results VALUES(?,?,?,?,?,?,?,?)",
                ("old", NOW, 0, 0, 0, -2, "loss", "{}"),
            )
            await d.commit()
    return a, p, q


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "hedged",
        "margin",
        "funding",
        "negative_edge",
        "stale_cex",
        "identity",
        "daily_loss",
    ],
)
def test_admission_uses_identity_private_account_and_realized_risk(tmp_path, fault):
    async def run():
        a, p, q = await setup(tmp_path, fault)
        if fault == "none":
            assert await a(p, q) is True
            assert Decimal(a.latest["ceiling"]) > Decimal("0.05")
            assert a.latest["mode"].endswith("NOT_REALIZED")
        else:
            with pytest.raises(ValueError):
                await a(p, q)

    asyncio.run(run())
