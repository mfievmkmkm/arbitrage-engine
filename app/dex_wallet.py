"""Isolated EVM transaction boundary. Never auto-approves, retries or replaces.

This backend does not authorize a CEX/DEX strategy. Callers must reserve the
common LIVE trade first and provide separate entry/exit authority. Ethereum
mainnet only: finalized blocks and standard ERC20 transfers are required.
"""

import asyncio
import copy
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
import aiosqlite
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_utils import keccak, to_checksum_address
from .dex_firm_simulation import Envelope, address, integer, HEX

HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
CHECKS = (
    "wallet_isolated",
    "preapproved_allowance",
    "dual_rpc",
    "receipt_finality",
    "token_cashflow",
    "nonce_recovery",
    "cex_hedge",
    "cex_partial_recovery",
    "private_funding",
    "entry_exit_e2e",
    "restart",
    "micro_canary",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_tx_intents(intent_id TEXT PRIMARY KEY,trade_id TEXT,chain_id INTEGER,wallet TEXT,nonce INTEGER,phase TEXT,tx_hash TEXT,created_at REAL,updated_at REAL,payload TEXT,UNIQUE(chain_id,wallet,nonce));
CREATE TABLE IF NOT EXISTS wallet_tx_events(id INTEGER PRIMARY KEY,intent_id TEXT,ts REAL,phase TEXT,payload TEXT);
"""


def quantity(x):
    if not isinstance(x, str) or not re.fullmatch(
        r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", x
    ):
        raise ValueError("WALLET_RPC_QUANTITY_INVALID")
    return integer(str(int(x, 16)), "WALLET_RPC_UINT", False)


def digest(data):
    if not isinstance(data, str) or not HEX.fullmatch(data):
        raise ValueError("WALLET_CALLDATA_INVALID")
    return hashlib.sha256(bytes.fromhex(data[2:])).hexdigest()


def accepted(path, chain, wallet, venue, sell, buy, now):
    try:
        d = json.loads(Path(path).read_text())
        if (
            type(d.get("version")) is not int
            or d["version"] != 1
            or not isinstance(d.get("evidence_id"), str)
            or not d["evidence_id"].strip()
        ):
            return False
        start, expiry = d["verified_at"], d["expires_at"]
        if (
            any(
                type(x) not in (int, float) or not math.isfinite(x)
                for x in (start, expiry, now)
            )
            or not start <= now < expiry
            or expiry - start > 86400
        ):
            return False
        return (
            type(d["chain_id"]) is int
            and d["chain_id"] == chain == 1
            and address(d["wallet"]) == address(wallet)
            and d["cex_venue"] == venue
            and set(map(address, d["tokens"])) == {address(sell), address(buy)}
            and all(d.get("checks", {}).get(k) is True for k in CHECKS)
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class Policy:
    wallet: str
    cex_venue: str
    acceptance_path: str
    targets: tuple
    max_sell_raw: dict
    max_gas_raw: int
    enabled: bool = False

    def check(self, envelope, now):
        if self.enabled is not True or not isinstance(envelope, Envelope):
            raise ValueError("WALLET_EXPLICIT_AUTHORITY_REQUIRED")
        q, tx = envelope.proof, envelope.transaction
        if type(q.get("chain_id")) is not int or any(
            type(tx.get(k)) is not int for k in ("chainId", "gas", "gasPrice", "value")
        ):
            raise ValueError("WALLET_INTEGER_FIELDS_REQUIRED")
        wallet = address(self.wallet)
        if (
            envelope.taker != wallet
            or q.get("ok") is not True
            or q.get("simulation_verified") is not True
            or q.get("chain_id") != 1
        ):
            raise ValueError("WALLET_QUOTE_SCOPE_INVALID")
        if not accepted(
            self.acceptance_path,
            1,
            wallet,
            self.cex_venue,
            q["sell_token"],
            q["buy_token"],
            now,
        ):
            raise ValueError("WALLET_DEDICATED_ACCEPTANCE_REQUIRED")
        if not q["ts"] <= q["received_at"] <= now or now - q["ts"] > 15:
            raise ValueError("WALLET_QUOTE_STALE")
        if (
            set(tx) != {"to", "data", "value", "gas", "gasPrice", "chainId"}
            or tx["chainId"] != 1
            or tx["value"] != 0
        ):
            raise ValueError("WALLET_TRANSACTION_FIELDS_INVALID")
        if address(tx["to"]) not in set(map(address, self.targets)) or tx[
            "data"
        ].lower().startswith("0x095ea7b3"):
            raise ValueError("WALLET_TARGET_OR_APPROVAL_FORBIDDEN")
        amount = integer(q["sell_amount_raw"], "WALLET_AMOUNT")
        requested = integer(q["requested_amount_raw"], "WALLET_REQUESTED")
        if q["quote_mode"] == "exact_out":
            if (
                integer(q["max_sell_amount_raw"], "WALLET_MAX_INPUT") != amount
                or integer(q["buy_amount_raw"], "WALLET_EXACT_BUY") != requested
                or integer(q["min_buy_amount_raw"], "WALLET_MINIMUM") != requested
            ):
                raise ValueError("WALLET_EXACT_OUT_BOUND_CONFLICT")
        elif (
            q["quote_mode"] != "exact_in"
            or requested != amount
            or integer(q["min_buy_amount_raw"], "WALLET_MINIMUM")
            > integer(q["buy_amount_raw"], "WALLET_BUY")
        ):
            raise ValueError("WALLET_EXACT_IN_BOUND_CONFLICT")
        if amount > integer(self.max_sell_raw.get(q["sell_token"]), "WALLET_SELL_CAP"):
            raise ValueError("WALLET_SELL_CAP_EXCEEDED")
        gas = integer(tx["gas"], "WALLET_GAS") * integer(
            tx["gasPrice"], "WALLET_GAS_PRICE"
        )
        if max(gas, integer(q["network_fee_raw"], "WALLET_NETWORK_FEE")) > integer(
            self.max_gas_raw, "WALLET_GAS_CAP"
        ):
            raise ValueError("WALLET_GAS_CAP_EXCEEDED")
        minimum = integer(q["min_buy_amount_raw"], "WALLET_MINIMUM")
        fingerprint = hashlib.sha256(
            json.dumps(
                dict(
                    chain=1,
                    block_hash=q["block_hash"],
                    target=address(tx["to"]),
                    data=tx["data"],
                    sell=q["sell_token"],
                    buy=q["buy_token"],
                    amount=amount,
                    minimum=minimum,
                    quote_mode=q["quote_mode"],
                    requested=integer(q["requested_amount_raw"], "WALLET_REQUESTED"),
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if fingerprint != q["quote_fingerprint"]:
            raise ValueError("WALLET_QUOTE_MUTATED")


@dataclass(frozen=True)
class Signed:
    raw: bytes = field(repr=False)
    hash: str


class Signer:
    """Explicitly constructed from a configured isolated key, never loaded on import."""

    def __init__(self, key, expected_wallet):
        self.account = Account.from_key(key)
        if address(self.account.address) != address(expected_wallet):
            raise ValueError("WALLET_SIGNER_ADDRESS_MISMATCH")

    async def sign(self, tx):
        signed = self.account.sign_transaction(
            dict(tx, to=to_checksum_address(tx["to"]))
        )
        return Signed(bytes(signed.raw_transaction), "0x" + bytes(signed.hash).hex())


def verify_signed(signed, tx, wallet):
    if not isinstance(signed, Signed) or len(signed.raw) > 131072:
        raise ValueError("WALLET_SIGNED_ENVELOPE_INVALID")
    if "0x" + keccak(signed.raw).hex() != signed.hash or address(
        Account.recover_transaction(signed.raw)
    ) != address(wallet):
        raise ValueError("WALLET_SIGNATURE_SCOPE_INVALID")
    decoded = Transaction.from_bytes(signed.raw).as_dict()
    if decoded["v"] < 35 or (decoded["v"] - 35) // 2 != tx["chainId"]:
        raise ValueError("WALLET_SIGNATURE_CHAIN_INVALID")
    for key in ("nonce", "gas", "gasPrice", "value"):
        if decoded[key] != tx[key]:
            raise ValueError("WALLET_SIGNED_TRANSACTION_CHANGED")
    if address("0x" + decoded["to"].hex()) != address(tx["to"]) or decoded[
        "data"
    ] != bytes.fromhex(tx["data"][2:]):
        raise ValueError("WALLET_SIGNED_CALL_CHANGED")


class Journal:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.commit()

    async def get(self, iid):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT * FROM wallet_tx_intents WHERE intent_id=?", (iid,)
            ) as c:
                row = await c.fetchone()
        return dict(row) if row else None

    async def reserve(self, iid, tid, wallet, nonce, payload, closing=False):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?", (tid,)
            ) as c:
                owner = await c.fetchone()
            expected = "DEX_EXIT_SUBMITTING" if closing else "PLANNED"
            if not owner or owner[0] != expected:
                return False
            meta = json.loads(owner[1])
            if (
                meta.get("strategy") != "cex_dex"
                or meta.get("wallet_address") != wallet
                or meta.get("wallet_chain") != 1
                or meta.get("cex_venue") != payload["cex_venue"]
                or meta.get("wallet_quote_fingerprint")
                != payload["proof"]["quote_fingerprint"]
            ):
                return False
            try:
                await d.execute(
                    "INSERT INTO wallet_tx_intents VALUES(?,?,?,?,?,'SIGNING',NULL,?,?,?)",
                    (
                        iid,
                        tid,
                        1,
                        wallet,
                        nonce,
                        self.clock(),
                        self.clock(),
                        json.dumps(payload),
                    ),
                )
            except aiosqlite.IntegrityError:
                return False
            await d.execute(
                "UPDATE live_trades SET phase='DEX_WALLET_PENDING',updated_at=? WHERE trade_id=? AND phase=?",
                (self.clock(), tid, expected),
            )
            await d.commit()
        return True

    async def transition(self, iid, expected, phase, tx_hash=None, **fields):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT phase,payload,tx_hash FROM wallet_tx_intents WHERE intent_id=?",
                (iid,),
            ) as c:
                old = await c.fetchone()
            if (
                not old
                or old[0] not in expected
                or (old[2] and tx_hash and old[2] != tx_hash)
            ):
                return False
            payload = json.loads(old[1])
            payload.update(fields)
            await d.execute(
                "UPDATE wallet_tx_intents SET phase=?,tx_hash=COALESCE(tx_hash,?),updated_at=?,payload=? WHERE intent_id=?",
                (phase, tx_hash, self.clock(), json.dumps(payload), iid),
            )
            await d.execute(
                "INSERT INTO wallet_tx_events(intent_id,ts,phase,payload) VALUES(?,?,?,?)",
                (iid, self.clock(), phase, json.dumps(fields)),
            )
            await d.commit()
        return True


class RPC:
    READS = {
        "eth_chainId",
        "eth_getTransactionCount",
        "eth_getTransactionReceipt",
        "eth_getTransactionByHash",
        "eth_getBlockByNumber",
        "eth_getBalance",
        "eth_call",
        "eth_gasPrice",
        "eth_getCode",
    }

    def __init__(self, url, session):
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("WALLET_HTTPS_RPC_REQUIRED")
        self.url, self.session, self.counter = url, session, 0

    async def call(self, method, params):
        if method not in self.READS and method != "eth_sendRawTransaction":
            raise ValueError("WALLET_RPC_METHOD_FORBIDDEN")
        self.counter += 1
        rid = self.counter
        async with self.session.post(
            self.url,
            json=dict(jsonrpc="2.0", id=rid, method=method, params=params),
            timeout=8,
        ) as r:
            if r.status != 200:
                raise ValueError("WALLET_RPC_UNAVAILABLE")
            row = await r.json()
        if (
            not isinstance(row, dict)
            or row.get("id") != rid
            or row.get("error")
            or "result" not in row
        ):
            raise ValueError("WALLET_RPC_UNVERIFIED")
        return row["result"]


class Sender:
    def __init__(
        self, journal, primary, secondary, signer, policy, authority, clock=time.time
    ):
        if primary is secondary:
            raise ValueError("WALLET_INDEPENDENT_RPC_REQUIRED")
        (
            self.journal,
            self.primary,
            self.secondary,
            self.signer,
            self.policy,
            self.authority,
            self.clock,
        ) = (journal, primary, secondary, signer, policy, authority, clock)

    async def send(self, tid, iid, envelope, closing=False):
        if await self.journal.get(iid):
            return dict(status="RECONCILE_REQUIRED", intent_id=iid)
        envelope = copy.deepcopy(envelope)
        self.policy.check(envelope, self.clock())
        if self.authority(tid, closing) is not True:
            raise ValueError("WALLET_STRATEGY_AUTHORITY_REQUIRED")
        wallet = address(self.policy.wallet)
        readings = await asyncio.gather(
            *(
                r.call(method, params)
                for r in (self.primary, self.secondary)
                for method, params in (
                    ("eth_chainId", []),
                    ("eth_getTransactionCount", [wallet, "latest"]),
                    ("eth_getTransactionCount", [wallet, "pending"]),
                )
            )
        )
        if (
            quantity(readings[0]) != 1
            or readings[:3] != readings[3:]
            or readings[1] != readings[2]
        ):
            raise ValueError("WALLET_CHAIN_NONCE_UNRESOLVED")
        tx = dict(envelope.transaction, nonce=quantity(readings[1]))

        async def preflight(rpc):
            header, latest, gas, code = await asyncio.gather(
                rpc.call(
                    "eth_getBlockByNumber", [hex(envelope.proof["block_number"]), False]
                ),
                rpc.call("eth_getBlockByNumber", ["latest", False]),
                rpc.call("eth_gasPrice", []),
                rpc.call("eth_getCode", [wallet, "latest"]),
            )
            if (
                header.get("hash") != envelope.proof["block_hash"]
                or not 0
                <= quantity(latest["number"]) - envelope.proof["block_number"]
                <= 2
                or quantity(gas) > tx["gasPrice"]
                or code != "0x"
            ):
                raise ValueError("WALLET_FRESH_CHAIN_GAS_OR_EOA_UNVERIFIED")
            call = dict(
                to=tx["to"],
                data=tx["data"],
                gas=hex(tx["gas"]),
                gasPrice=hex(tx["gasPrice"]),
                value="0x0",
                **{"from": wallet}
            )
            result = await rpc.call(
                "eth_call", [call, hex(envelope.proof["block_number"])]
            )
            if not isinstance(result, str) or not HEX.fullmatch(result):
                raise ValueError("WALLET_PRE_SEND_SIMULATION_UNVERIFIED")

        await asyncio.gather(preflight(self.primary), preflight(self.secondary))
        payload = dict(
            proof=envelope.proof,
            transaction={k: v for k, v in tx.items() if k != "data"},
            data_hash=digest(tx["data"]),
            closing=closing,
            cex_venue=self.policy.cex_venue,
        )
        self.policy.check(envelope, self.clock())
        if not await self.journal.reserve(
            iid, tid, wallet, tx["nonce"], payload, closing
        ):
            return dict(status="BLOCKED", reason="WALLET_CAPACITY_OR_NONCE_CONFLICT")
        try:
            signed = await self.signer.sign(tx)
            verify_signed(signed, tx, wallet)
            if not await self.journal.transition(
                iid, ("SIGNING",), "BROADCASTING", signed.hash
            ):
                raise ValueError("WALLET_SIGNING_CLAIM_CONFLICT")
            self.policy.check(envelope, self.clock())
            if self.authority(tid, closing) is not True:
                raise ValueError("WALLET_PRE_SEND_AUTHORITY_LOST")
            # Only this claimed path may broadcast; raw signatures never enter storage.
            result = await self.primary.call(
                "eth_sendRawTransaction", ["0x" + signed.raw.hex()]
            )
            if not isinstance(result, str) or result.lower() != signed.hash.lower():
                raise ValueError("WALLET_BROADCAST_HASH_UNVERIFIED")
            await self.journal.transition(iid, ("BROADCASTING",), "PENDING")
            return dict(status="PENDING", intent_id=iid, tx_hash=signed.hash)
        except (Exception, asyncio.CancelledError) as e:
            await asyncio.shield(
                self.journal.transition(
                    iid,
                    ("SIGNING", "BROADCASTING"),
                    "UNKNOWN",
                    reason="WALLET_SEND_UNKNOWN_RECONCILE",
                )
            )
            if isinstance(e, asyncio.CancelledError):
                raise
            return dict(
                status="UNKNOWN", intent_id=iid, reason="WALLET_SEND_UNKNOWN_RECONCILE"
            )


class Reader:
    def __init__(self, journal, primary, secondary, clock=time.time):
        if primary is secondary:
            raise ValueError("WALLET_INDEPENDENT_RPC_REQUIRED")
        self.journal, self.primary, self.secondary, self.clock = (
            journal,
            primary,
            secondary,
            clock,
        )

    async def _one(self, rpc, row):
        if row["chain_id"] != 1 or quantity(await rpc.call("eth_chainId", [])) != 1:
            raise ValueError("WALLET_RECEIPT_CHAIN_MISMATCH")
        receipt = await rpc.call("eth_getTransactionReceipt", [row["tx_hash"]])
        if receipt is None:
            raise ValueError("WALLET_RECEIPT_PENDING")
        if (
            receipt.get("transactionHash", "").lower() != row["tx_hash"].lower()
            or receipt.get("removed") is True
        ):
            raise ValueError("WALLET_RECEIPT_SCOPE_INVALID")
        block = quantity(receipt["blockNumber"])
        if block < 1 or not HASH.fullmatch(receipt["blockHash"]):
            raise ValueError("WALLET_RECEIPT_BLOCK_INVALID")
        header, final, tx = await asyncio.gather(
            rpc.call("eth_getBlockByNumber", [hex(block), False]),
            rpc.call("eth_getBlockByNumber", ["finalized", False]),
            rpc.call("eth_getTransactionByHash", [row["tx_hash"]]),
        )
        if (
            not isinstance(header, dict)
            or not isinstance(final, dict)
            or header["hash"] != receipt["blockHash"]
            or quantity(header["number"]) != block
            or quantity(final["number"]) < block
        ):
            raise ValueError("WALLET_RECEIPT_NOT_FINALIZED")
        payload = json.loads(row["payload"])
        wanted, q = payload["transaction"], payload["proof"]
        if (
            not isinstance(tx, dict)
            or address(tx["from"]) != row["wallet"]
            or address(tx["to"]) != address(wanted["to"])
            or tx["blockHash"] != receipt["blockHash"]
            or tx["hash"].lower() != row["tx_hash"].lower()
            or digest(tx["input"]) != payload["data_hash"]
        ):
            raise ValueError("WALLET_MINED_TRANSACTION_CHANGED")
        for field in ("nonce", "value", "gas", "gasPrice"):
            if quantity(tx[field]) != wanted[field]:
                raise ValueError("WALLET_MINED_FIELDS_CHANGED")
        status = quantity(receipt["status"])
        used, price = quantity(receipt["gasUsed"]), quantity(
            receipt["effectiveGasPrice"]
        )
        if (
            status not in (0, 1)
            or not 0 < used <= wanted["gas"]
            or price != wanted["gasPrice"]
            or receipt.get("l1Fee") not in (None, "0x0")
        ):
            raise ValueError("WALLET_RECEIPT_GAS_INVALID")
        deltas = {q["sell_token"]: 0, q["buy_token"]: 0}
        seen = set()
        logs = receipt.get("logs")
        if not isinstance(logs, list):
            raise ValueError("WALLET_LOGS_MISSING")
        for log in logs:
            token = str(log.get("address", "")).lower()
            if token not in deltas:
                continue
            topics = log.get("topics", [])
            if not topics or topics[0].lower() != TRANSFER:
                continue
            if (
                len(topics) != 3
                or any(not HASH.fullmatch(t) for t in topics)
                or any(t[2:26] != "0" * 24 for t in topics[1:])
                or not HASH.fullmatch(log.get("data", ""))
            ):
                raise ValueError("WALLET_TRANSFER_ENCODING_INVALID")
            idx = quantity(log["logIndex"])
            if (
                idx in seen
                or log.get("removed") is not False
                or log.get("blockHash") != receipt["blockHash"]
                or log.get("transactionHash", "").lower() != row["tx_hash"].lower()
            ):
                raise ValueError("WALLET_TRANSFER_SCOPE_INVALID")
            seen.add(idx)
            amount = int(log["data"], 16)
            sender, receiver = (
                "0x" + topics[1][-40:].lower(),
                "0x" + topics[2][-40:].lower(),
            )
            deltas[token] += amount * (
                (receiver == row["wallet"]) - (sender == row["wallet"])
            )

        async def balances(tag):
            tokens = await asyncio.gather(
                *(
                    rpc.call(
                        "eth_call",
                        [
                            dict(
                                to=t,
                                data="0x70a08231" + row["wallet"][2:].rjust(64, "0"),
                            ),
                            tag,
                        ],
                    )
                    for t in deltas
                )
            )
            if any(not isinstance(x, str) or not HASH.fullmatch(x) for x in tokens):
                raise ValueError("WALLET_TOKEN_BALANCE_UNVERIFIED")
            native, nonce = await asyncio.gather(
                rpc.call("eth_getBalance", [row["wallet"], tag]),
                rpc.call("eth_getTransactionCount", [row["wallet"], tag]),
            )
            return (
                dict(zip(deltas, (int(x, 16) for x in tokens))),
                quantity(native),
                quantity(nonce),
            )

        before, after = await asyncio.gather(
            balances(hex(block - 1)), balances(hex(block))
        )
        if (
            before[2] != row["nonce"]
            or after[2] != row["nonce"] + 1
            or after[1] - before[1] != -used * price
        ):
            raise ValueError("WALLET_ISOLATION_OR_GAS_BALANCE_CONFLICT")
        if any(after[0][t] - before[0][t] != delta for t, delta in deltas.items()):
            raise ValueError("WALLET_TRANSFER_BALANCE_CONFLICT")
        sold, bought = -deltas[q["sell_token"]], deltas[q["buy_token"]]
        if status == 0 and (sold or bought):
            raise ValueError("WALLET_REVERT_TRANSFER_CONFLICT")
        if status == 1:
            if q["quote_mode"] == "exact_in":
                valid = sold == int(q["sell_amount_raw"]) and bought >= int(
                    q["min_buy_amount_raw"]
                )
            else:
                valid = 0 < sold <= int(q["max_sell_amount_raw"]) and bought == int(
                    q["buy_amount_raw"]
                )
            if not valid:
                raise ValueError("WALLET_ACTUAL_MIN_MAX_CONFLICT")
        if (await rpc.call("eth_getBlockByNumber", [hex(block), False]))[
            "hash"
        ] != receipt["blockHash"]:
            raise ValueError("WALLET_RECEIPT_REORG")
        return dict(
            verified=True,
            finalized=True,
            chain_id=1,
            tx_hash=row["tx_hash"],
            block_number=block,
            block_hash=receipt["blockHash"],
            status=status,
            sold_raw=str(sold),
            bought_raw=str(bought),
            gas_paid_raw=str(used * price),
            token_deltas={k: str(v) for k, v in deltas.items()},
            before={k: str(v) for k, v in before[0].items()},
            after={k: str(v) for k, v in after[0].items()},
        )

    async def reconcile(self, iid):
        row = await self.journal.get(iid)
        if not row or not row["tx_hash"]:
            return dict(status="UNKNOWN", reason="WALLET_SIGNING_OR_HASH_UNRESOLVED")
        try:
            proofs = await asyncio.gather(
                self._one(self.primary, row), self._one(self.secondary, row)
            )
            if proofs[0] != proofs[1]:
                raise ValueError("WALLET_DUAL_RPC_CONFLICT")
            phase = "FINALIZED_SUCCESS" if proofs[0]["status"] else "FINALIZED_REVERT"
            if row["phase"] in ("FINALIZED_SUCCESS", "FINALIZED_REVERT"):
                if json.loads(row["payload"]).get("receipt") != proofs[0]:
                    raise ValueError("WALLET_FINAL_RECEIPT_CHANGED")
            elif not await self.journal.transition(
                iid, ("PENDING", "BROADCASTING", "UNKNOWN"), phase, receipt=proofs[0]
            ):
                raise ValueError("WALLET_RECEIPT_CLAIM_CONFLICT")
            return dict(status=phase, proof=proofs[0])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A receipt becoming unavailable never authorizes resend or frees capacity.
            return dict(
                status="HOLD",
                reason=(
                    str(e)
                    if isinstance(e, ValueError)
                    else "WALLET_RECEIPT_UNAVAILABLE"
                ),
            )
