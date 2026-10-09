import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
import aiosqlite
import pytest
from eth_account import Account
from app.dex_wallet import (
    Journal,
    Sender,
    Reader,
    Signer,
    Policy,
    Signed,
    CHECKS,
    TRANSFER,
    verify_signed,
    quantity,
    accepted,
)
from app.dex_firm_simulation import Envelope
from app.live_trade_store import Store
from tests.test_dex_firm_simulation import Sim, registry, SELL, BUY, TARGET, HASH, NOW

MINIMUM = 49400000000000000
BLOCK_HASH = "0x" + "b" * 64


def topic(address):
    return "0x" + address[2:].rjust(64, "0")


class Node:
    def __init__(self, wallet):
        self.wallet, self.calls, self.sent = wallet, [], None
        self.fault = ""
        self.status = 1

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return "0x2" if self.fault == "chain" else "0x1"
        if method == "eth_getCode":
            return "0x01" if self.fault == "delegated" else "0x"
        if method == "eth_gasPrice":
            return hex(2 * 10**9 if self.fault == "gas" else 10**9)
        if method == "eth_getTransactionCount":
            tag = params[1]
            return hex(
                8
                if tag == hex(101) or self.fault == "pending" and tag == "pending"
                else 7
            )
        if method == "eth_getBlockByNumber":
            tag = params[0]
            number = (
                102 if tag == "finalized" else 100 if tag == "latest" else int(tag, 16)
            )
            if self.fault == "unfinalized" and tag == "finalized":
                number = 100
            h = HASH if number == 100 else BLOCK_HASH
            if self.fault == "reorg" and number == 101:
                h = HASH
            return dict(number=hex(number), hash=h, timestamp=hex(NOW - 5))
        if method == "eth_sendRawTransaction":
            from eth_utils import keccak

            self.sent = "0x" + keccak(bytes.fromhex(params[0][2:])).hex()
            if self.fault == "timeout":
                raise TimeoutError("injected")
            return self.sent
        if method == "eth_getTransactionReceipt":
            if self.fault == "pending_receipt":
                return None
            logs = []
            if self.status:
                for idx, (token, sender, receiver, amount) in enumerate(
                    (
                        (SELL, self.wallet, TARGET, 4000000),
                        (BUY, TARGET, self.wallet, MINIMUM),
                    )
                ):
                    logs.append(
                        dict(
                            address=token,
                            topics=[TRANSFER, topic(sender), topic(receiver)],
                            data="0x" + hex(amount)[2:].rjust(64, "0"),
                            logIndex=hex(idx),
                            removed=False,
                            blockHash=BLOCK_HASH,
                            transactionHash=params[0],
                        )
                    )
                if self.fault == "duplicate":
                    logs.append(logs[0])
                if self.fault == "tax":
                    logs[1]["data"] = "0x" + hex(MINIMUM - 1)[2:].rjust(64, "0")
            return dict(
                transactionHash=params[0],
                blockNumber=hex(101),
                blockHash=BLOCK_HASH,
                status=hex(self.status),
                gasUsed=hex(21000),
                effectiveGasPrice=hex(10**9),
                logs=logs,
            )
        if method == "eth_getTransactionByHash":
            return dict(
                hash=params[0],
                blockHash=BLOCK_HASH,
                **{"from": self.wallet},
                to=TARGET,
                nonce="0x7",
                value="0x0",
                gas=hex(21000),
                gasPrice=hex(10**9),
                input="0xabcdef00" if self.fault == "call_changed" else "0x12345678"
            )
        if method == "eth_getBalance":
            return hex(10**18 - (21000 * 10**9 if params[1] == hex(101) else 0))
        if method == "eth_call":
            if params[0]["data"] == "0x12345678":
                return "0x"
            after = params[1] == hex(101) and self.status
            value = (
                10000000 - (4000000 if after else 0)
                if params[0]["to"] == SELL
                else 10**18 + (MINIMUM if after else 0)
            )
            if self.fault == "balance" and after:
                value += 1
            return "0x" + hex(value)[2:].rjust(64, "0")
        raise AssertionError(method)


async def setup(tmp_path):
    account = Account.create()
    wallet = account.address.lower()
    envelope = await Sim().firm(
        1, SELL, BUY, "4000000", wallet, registry(), execution_envelope=True
    )
    assert isinstance(envelope, Envelope)
    certificate = dict(
        version=1,
        evidence_id="OFFLINE_FIXTURE_NOT_CERTIFICATION",
        verified_at=900,
        expires_at=1100,
        chain_id=1,
        wallet=wallet,
        cex_venue="a",
        tokens=[SELL, BUY],
        checks={k: True for k in CHECKS},
    )
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(certificate))
    policy = Policy(
        wallet, "a", str(path), (TARGET,), {SELL: 4000000, BUY: MINIMUM}, 10**15, True
    )
    store = Store(tmp_path / "a.db")
    await store.init()
    assert await store.reserve_entry(
        "t",
        strategy="cex_dex",
        wallet_address=wallet,
        wallet_chain=1,
        cex_venue="a",
        wallet_quote_fingerprint=envelope.proof["quote_fingerprint"],
        symbol="X/USDT:USDT",
        long_venue="wallet:1",
        short_venue="a",
        planned_long=0.0494,
        planned_short=0.049,
    )
    journal = Journal(store.path, clock=lambda: NOW)
    await journal.init()
    a, b = Node(wallet), Node(wallet)
    sender = Sender(
        journal,
        a,
        b,
        Signer(account.key, wallet),
        policy,
        lambda *args: True,
        clock=lambda: NOW,
    )
    reader = Reader(journal, a, b, clock=lambda: NOW)
    return envelope, policy, journal, a, b, sender, reader


def test_real_signature_claim_broadcast_and_finalized_cashflow(tmp_path):
    async def run():
        envelope, policy, journal, a, b, sender, reader = await setup(tmp_path)
        assert "12345678" not in repr(envelope) and policy.wallet not in repr(envelope)
        result = await sender.send("t", "i", envelope)
        assert result["status"] == "PENDING"
        row = await journal.get("i")
        assert row["tx_hash"] == a.sent
        assert (
            "12345678" not in row["payload"] and a.calls[-1][1][0] not in row["payload"]
        )
        proof = await reader.reconcile("i")
        assert proof["status"] == "FINALIZED_SUCCESS", proof
        assert proof["proof"]["sold_raw"] == "4000000"
        assert proof["proof"]["bought_raw"] == str(MINIMUM)
        assert proof["proof"]["gas_paid_raw"] == "21000000000000"
        assert (await sender.send("t", "i", envelope))["status"] == "RECONCILE_REQUIRED"
        assert sum(m == "eth_sendRawTransaction" for m, _ in a.calls) == 1
        assert (await reader.reconcile("i"))["status"] == "FINALIZED_SUCCESS"
        async with aiosqlite.connect(journal.path) as d:
            async with d.execute(
                "SELECT phase FROM live_trades WHERE trade_id='t'"
            ) as c:
                assert (await c.fetchone())[0] == "DEX_WALLET_PENDING"

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["chain", "pending", "delegated", "gas"])
def test_preflight_failure_never_signs_or_broadcasts(tmp_path, fault):
    async def run():
        envelope, _, journal, a, b, sender, _ = await setup(tmp_path)
        b.fault = fault
        with pytest.raises(ValueError):
            await sender.send("t", "i", envelope)
        assert not await journal.get("i") and not a.sent

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "pending_receipt",
        "unfinalized",
        "reorg",
        "duplicate",
        "tax",
        "balance",
        "call_changed",
        "chain",
    ],
)
def test_unverified_receipt_never_flat_or_resends(tmp_path, fault):
    async def run():
        envelope, _, journal, a, b, sender, reader = await setup(tmp_path)
        await sender.send("t", "i", envelope)
        b.fault = fault
        result = await reader.reconcile("i")
        assert result["status"] == "HOLD", result
        assert (await journal.get("i"))["phase"] == "PENDING"
        assert (await sender.send("t", "i", envelope))["status"] == "RECONCILE_REQUIRED"

    asyncio.run(run())


def test_timeout_retains_hash_and_reconciles_without_repeat(tmp_path):
    async def run():
        envelope, _, journal, a, b, sender, reader = await setup(tmp_path)
        a.fault = "timeout"
        assert (await sender.send("t", "i", envelope))["status"] == "UNKNOWN"
        assert (await journal.get("i"))["tx_hash"] == a.sent
        assert (await reader.reconcile("i"))["status"] == "FINALIZED_SUCCESS"
        assert (await sender.send("t", "i", envelope))["status"] == "RECONCILE_REQUIRED"

    asyncio.run(run())


def test_known_revert_has_actual_gas_no_token_income(tmp_path):
    async def run():
        envelope, _, journal, a, b, sender, reader = await setup(tmp_path)
        await sender.send("t", "i", envelope)
        a.status = b.status = 0
        result = await reader.reconcile("i")
        assert result["status"] == "FINALIZED_REVERT"
        assert result["proof"]["sold_raw"] == result["proof"]["bought_raw"] == "0"
        assert result["proof"]["gas_paid_raw"] == "21000000000000"

    asyncio.run(run())


def test_two_workers_cannot_sign_same_wallet_nonce(tmp_path):
    async def run():
        envelope, _, journal, a, b, sender, _ = await setup(tmp_path)
        other = Sender(
            journal,
            a,
            b,
            sender.signer,
            sender.policy,
            sender.authority,
            clock=lambda: NOW,
        )
        results = await asyncio.gather(
            sender.send("t", "i", envelope), other.send("t", "j", envelope)
        )
        assert sum(r["status"] == "PENDING" for r in results) == 1
        assert sum(m == "eth_sendRawTransaction" for m, _ in a.calls) == 1

    asyncio.run(run())


def test_lost_authority_after_signing_does_not_broadcast(tmp_path):
    async def run():
        envelope, _, journal, a, _, sender, _ = await setup(tmp_path)
        gate = [True]
        original = sender.signer.sign

        async def sign(tx):
            result = await original(tx)
            gate[0] = False
            return result

        sender.signer.sign = sign
        sender.authority = lambda *args: gate[0]
        assert (await sender.send("t", "i", envelope))["status"] == "UNKNOWN"
        assert not a.sent and (await journal.get("i"))["tx_hash"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    ["calldata", "amount", "wallet", "expired", "disabled", "approval", "gas_cap"],
)
def test_signing_boundary_rejects_mutation_and_missing_authority(tmp_path, fault):
    async def run():
        envelope, policy, journal, a, _, sender, _ = await setup(tmp_path)
        tx, q = copy.deepcopy(envelope.transaction), copy.deepcopy(envelope.proof)
        if fault == "calldata":
            tx["data"] = "0xabcdef00"
        if fault == "amount":
            q["sell_amount_raw"] = "4000001"
        if fault == "wallet":
            envelope = replace(envelope, taker=TARGET)
        if fault == "expired":
            q["ts"] = 980
        if fault == "disabled":
            sender.policy = replace(policy, enabled=False)
        if fault == "approval":
            tx["data"] = "0x095ea7b3"
        if fault == "gas_cap":
            sender.policy = replace(policy, max_gas_raw=1)
        envelope = replace(envelope, proof=q, transaction=tx)
        with pytest.raises(ValueError):
            await sender.send("t", "i", envelope)
        assert not a.sent and not await journal.get("i")

    asyncio.run(run())


@pytest.mark.parametrize("value", ["0x00", "0x01", "0x", "1", True, -1, "0x-1"])
def test_rpc_quantities_are_canonical_unsigned(value):
    with pytest.raises(ValueError):
        quantity(value)
