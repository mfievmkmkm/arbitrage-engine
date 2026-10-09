import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import pytest
from app.dex_live_costs import Reader, USDT_MAINNET
from app.dex_live_bridge import Plan
from app.private_funding_reader import Evidence
from tests.test_dex_live_bridge import NOW, ASSET, WALLET


class Gas:
    id = "offline-public-book"

    def market(self, symbol):
        return dict(spot=True, active=True, base="ETH", quote="USDT")

    async def fetch_order_book(self, symbol, limit=20):
        return dict(
            symbol=symbol, timestamp=NOW * 1000, bids=[[1999, 10]], asks=[[2000, 10]]
        )


def plan():
    return Plan(
        "bybit",
        "X/USDT:USDT",
        WALLET,
        ASSET,
        USDT_MAINNET,
        18,
        6,
        "1",
        "forward",
        NOW - 100,
        "5",
        "0",
    )


@pytest.mark.parametrize(
    "fault",
    ["none", "immature", "wrong_quote", "gas_zero", "stale_book", "shallow_book"],
)
def test_private_costs_and_gas_are_scoped_and_executable(monkeypatch, fault):
    async def run():
        import app.dex_live_costs as module

        class Funding:
            def __init__(self, *args, **kwargs):
                pass

            async def collect(self, symbol, since, until):
                return Evidence(
                    fault != "immature",
                    -0.01,
                    (),
                    "IMMATURE" if fault == "immature" else "VERIFIED",
                    until,
                )

        monkeypatch.setattr(module, "FundingReader", Funding)
        p, gas = plan(), Gas()
        if fault == "wrong_quote":
            p = replace(p, quote=ASSET)
        if fault in ("stale_book", "shallow_book"):
            original = gas.fetch_order_book

            async def book(*args, **kwargs):
                b = await original(*args, **kwargs)
                if fault == "stale_book":
                    b["timestamp"] -= 2000
                else:
                    b["asks"][0][1] = 0.000001
                return b

            gas.fetch_order_book = book
        raw = 0 if fault == "gas_zero" else 10**14
        obs = dict(trade_id="t", wallet=dict(gas_raw=raw, hashes=["tx"]))
        reader = Reader({"bybit": object()}, gas, lambda: NOW)
        if fault != "none":
            with pytest.raises(ValueError):
                await reader(p, obs, NOW - 31)
        else:
            result = await reader(p, obs, NOW - 31)
            assert Decimal(result["gas_usdt"]) == Decimal("0.2")
            assert result["funding"] == "-0.01"
            assert result["gas_raw"] == str(raw)
            assert result["gas_valuation_evidence"]["method"].endswith("NOT_A_FILL")

    asyncio.run(run())
