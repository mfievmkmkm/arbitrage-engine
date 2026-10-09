import asyncio
import copy
import json
from types import SimpleNamespace as NS
import pytest
from app.dex_firm_simulation import Provider, Cycle, integer, registry_scope
from tests.test_spot_live_units import client, FS

SELL = "0x" + "1" * 40
BUY = "0x" + "2" * 40
TARGET = "0x" + "3" * 40
TAKER = "0x" + "4" * 40
HASH = "0x" + "a" * 64
NOW = 1000


def registry():
    return dict(
        version=1,
        evidence_id="contracts-reviewed",
        verified_at=900,
        expires_at=1100,
        chains={
            "1": dict(
                network_verified=True,
                transaction_targets=[TARGET],
                native_symbol="ETH/USDT:USDT",
                native_decimals=18,
                tokens={
                    SELL: dict(contract_verified=True, decimals=6, quote_usdt=True),
                    BUY: dict(
                        contract_verified=True,
                        decimals=18,
                        cex_venue="a",
                        cex_symbol=FS,
                        base="X",
                    ),
                },
            )
        },
    )


def raw():
    zero = dict(buyTaxBps="0", sellTaxBps="0", transferTaxBps="0")
    return dict(
        liquidityAvailable=True,
        sellToken=SELL,
        buyToken=BUY,
        sellAmount="4000000",
        buyAmount="49490000000000000",
        minBuyAmount="49400000000000000",
        issues=dict(
            allowance=None,
            balance=None,
            simulationIncomplete=False,
            invalidSourcesPassed=[],
        ),
        tokenMetadata=dict(sellToken=zero, buyToken=zero),
        transaction=dict(
            to=TARGET, data="0x12345678", value="0", gas="21000", gasPrice="1000000000"
        ),
        totalNetworkFee="21000000000000",
        blockNumber="100",
    )


class Response:
    def __init__(self, row):
        self.row = row
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def json(self):
        return self.row


class Http:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.row)


class Sim(Provider):
    def __init__(self, row=None):
        super().__init__(
            "secret", "https://rpc.example", Http(row or raw()), clock=lambda: NOW
        )
        self.calls = []
        self.fault = ""

    async def rpc(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return "0x2" if self.fault == "chain" else "0x1"
        if method == "eth_gasPrice":
            return hex(2 * 10**9 if self.fault == "gas" else 10**9)
        if method == "eth_getBalance":
            return "0x0" if self.fault == "gas_balance" else hex(10**18)
        if method == "eth_getBlockByNumber":
            row = dict(number=hex(100), timestamp=hex(NOW - 5), hash=HASH)
            if self.fault == "stale":
                row["timestamp"] = hex(NOW - 40)
            if self.fault == "head":
                row["number"] = hex(110)
            if (
                self.fault == "reorg"
                and sum(m == "eth_getBlockByNumber" for m, p in self.calls) > 2
            ):
                row["hash"] = "0x" + "b" * 64
            return row
        if method == "eth_getCode":
            return "0x" if self.fault == "code" else "0x1234"
        if method == "eth_call":
            if params[0]["data"] == "0x313ce567":
                return "0x" + hex(
                    7
                    if self.fault == "decimals"
                    else 6 if params[0]["to"] == SELL else 18
                )[2:].rjust(64, "0")
            if params[0]["data"].startswith("0x70a08231"):
                return "0x" + hex(0 if self.fault == "tokens" else 10**30)[2:].rjust(
                    64, "0"
                )
            if self.fault == "revert":
                raise ValueError("DEX_SWAP_REVERTED")
            return "0x"
        raise AssertionError(method)


async def firm(p, reg=None):
    return await p.firm(1, SELL, BUY, "4000000", TAKER, reg or registry())


def test_firm_quote_rpc_simulation_proof_is_scoped_and_read_only():
    async def go():
        p = Sim()
        r = await firm(p)
        assert r["ok"] and r["simulation_verified"], r
        assert not r["live_allowed"] and not r["paper_allowed"]
        assert (
            len(r["quote_fingerprint"]) == 64
            and r["min_buy_amount_raw"] == "49400000000000000"
        )
        text = json.dumps(r)
        assert (
            "secret" not in text
            and TAKER not in text
            and "rpc.example" not in text
            and "12345678" not in text
        )
        assert all(
            m not in ("eth_sendTransaction", "eth_sendRawTransaction")
            for m, _ in p.calls
        )
        assert p.session.calls[0][1]["params"]["slippageBps"] == 20

    asyncio.run(go())


@pytest.mark.parametrize(
    "fault",
    [
        "chain",
        "gas",
        "gas_balance",
        "stale",
        "head",
        "reorg",
        "code",
        "decimals",
        "tokens",
        "revert",
    ],
)
def test_chain_or_simulation_failure_never_proves_executable(fault):
    async def go():
        p = Sim()
        p.fault = fault
        r = await firm(p)
        assert not r["ok"] and not r["live_allowed"] and not r["paper_allowed"], r

    asyncio.run(go())


@pytest.mark.parametrize(
    "key,value",
    [
        ("sellToken", BUY),
        ("sellAmount", "1"),
        ("minBuyAmount", None),
        ("minBuyAmount", "1"),
        ("buyAmount", float("nan")),
        ("blockNumber", None),
        ("liquidityAvailable", False),
        ("issues", {}),
        (
            "issues",
            dict(
                allowance=None,
                balance=None,
                simulationIncomplete=True,
                invalidSourcesPassed=[],
            ),
        ),
        (
            "issues",
            dict(
                allowance={"spender": TARGET},
                balance=None,
                simulationIncomplete=False,
                invalidSourcesPassed=[],
            ),
        ),
        ("tokenMetadata", {}),
        (
            "transaction",
            dict(
                to=TARGET,
                data="0x12345678",
                gas="21000",
                gasPrice="1000000000",
                value="1",
            ),
        ),
        (
            "transaction",
            dict(
                to=SELL,
                data="0x12345678",
                gas="21000",
                gasPrice="1000000000",
                value="0",
            ),
        ),
        (
            "transaction",
            dict(
                to=TARGET, data="garbage", gas="21000", gasPrice="1000000000", value="0"
            ),
        ),
    ],
)
def test_provider_malformed_or_incomplete_quote_blocks(key, value):
    async def go():
        row = raw()
        row[key] = value
        p = Sim(row)
        r = await firm(p)
        assert not r["ok"] and not p.calls, r

    asyncio.run(go())


@pytest.mark.parametrize(
    "fault",
    ["expiry", "identity", "decimals", "network", "targets", "quote", "evidence"],
)
def test_registry_is_evidence_not_symbol_guess(fault):
    async def go():
        reg = registry()
        if fault == "expiry":
            reg["expires_at"] = 999
        if fault == "identity":
            reg["chains"]["1"]["tokens"][BUY]["contract_verified"] = False
        if fault == "decimals":
            reg["chains"]["1"]["tokens"][BUY]["decimals"] = True
        if fault == "network":
            reg["chains"]["1"]["network_verified"] = False
        if fault == "targets":
            reg["chains"]["1"]["transaction_targets"] = []
        if fault == "quote":
            reg["chains"]["1"]["tokens"][BUY]["quote_usdt"] = True
        if fault == "evidence":
            reg["evidence_id"] = ""
        p = Sim()
        r = await firm(p, reg)
        assert not r["ok"] and not p.calls and not p.session.calls

    asyncio.run(go())


@pytest.mark.parametrize("value", [True, 1.1, -1, "01", "1e9", "NaN", None, 2**256])
def test_raw_units_reject_rounded_or_unknown_numbers(value):
    with pytest.raises(ValueError):
        integer(value, "AMOUNT")


def test_cex_dex_simulation_uses_min_received_contracts_fees_and_gas():
    async def go():
        p = Sim()
        c = client()
        c.has.update(fetchTradingFee=True)

        async def fee(symbol):
            return dict(symbol=symbol, taker=0.001)

        async def book(symbol, limit=20):
            price = 100 if symbol == FS else 2000
            return dict(
                symbol=symbol,
                timestamp=NOW * 1000,
                bids=[[price, 1000]],
                asks=[[price + 0.01, 1000]],
            )

        c.fetch_trading_fee = fee
        c.fetch_order_book = book
        routes = [
            dict(
                chain_id=1,
                sell_token=SELL,
                buy_token=BUY,
                sell_amount_raw="4000000",
                taker=TAKER,
                label="X",
            )
        ]
        cycle = Cycle(p, routes, registry(), {"a": c}, {"a": c}, clock=lambda: NOW)
        row = (await cycle.cycle())[0]
        assert row["simulation_allowed"], row
        assert row["cex_contracts"] == 49 and row["base_qty"] == pytest.approx(0.049)
        assert row["gas_usd"] == pytest.approx(0.04200021)
        assert row["net_ceiling_model"] == pytest.approx(
            4.9 - 4 - 2 * 4.9 * 0.001 - 2 * row["gas_usd"] - 4.9 * 0.001
        )
        assert not row["live_allowed"] and not row["paper_allowed"]
        cycle.private = {}
        row = (await cycle.cycle())[0]
        assert not row["simulation_allowed"] and "FEE" in row["reason"]

    asyncio.run(go())


def test_rpc_adapter_forbids_write_methods():
    async def go():
        p = Provider("secret", "https://rpc.example", Http({}))
        with pytest.raises(ValueError, match="WRITE_FORBIDDEN"):
            await p.rpc("eth_sendRawTransaction", ["signed"])

    asyncio.run(go())
