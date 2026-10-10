"""Durable two-sided lifecycle; dependencies must supply private execution evidence.

This is an explicit session API, not a scanner or a source of write authority.
Restart observes. Only a new claimed stage or explicit bounded recovery sends.
No receipt, unknown order, external inventory movement or unvalued gas closes.
"""

import asyncio
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import aiosqlite
from .dex_firm_simulation import Envelope, address
from .live_trade_store import Store


def dec(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("DEX_NUMBER_INVALID")
    x = Decimal(str(value))
    if not x.is_finite() or not math.isfinite(float(x)):
        raise ValueError("DEX_NUMBER_INVALID")
    return x


def reason(error):
    message = str(error)
    return (
        message
        if isinstance(error, ValueError)
        and re.fullmatch(r"[A-Z0-9_:.-]{1,160}", message)
        else "DEX_EVIDENCE_UNAVAILABLE"
    )


@dataclass(frozen=True)
class Plan:
    venue: str
    symbol: str
    wallet: str
    asset: str
    quote: str
    asset_decimals: int
    quote_decimals: int
    contract_size: str
    direction: str
    opened_at: float
    budget: str
    safety: str

    def validate(self):
        if (
            not self.venue
            or not self.symbol.endswith("/USDT:USDT")
            or address(self.wallet) != self.wallet
            or address(self.asset) == address(self.quote)
            or self.direction not in ("forward", "reverse")
            or any(
                type(x) is not int or not 0 <= x <= 36
                for x in (self.asset_decimals, self.quote_decimals)
            )
            or not 0 < dec(self.budget) <= 5
            or dec(self.safety) < 0
            or dec(self.contract_size) <= 0
            or dec(self.opened_at) <= 0
        ):
            raise ValueError("DEX_PLAN_INVALID")
        if self.asset != address(self.asset) or self.quote != address(self.quote):
            raise ValueError("DEX_TOKEN_CASE_INVALID")


def wallet_flow(rows, tid, p):
    """Replay receipts in nonce order, enforcing continuity of both token balances."""
    total = {p.asset: 0, p.quote: 0}
    gas, previous, seen = 0, None, set()
    for row in sorted(rows, key=lambda r: r["nonce"]):
        data = json.loads(row["payload"])
        q, receipt = data["proof"], data.get("receipt")
        if (
            row["trade_id"] != tid
            or row["wallet"] != p.wallet
            or row["chain_id"] != 1
            or data.get("cex_venue") != p.venue
            or set((q["sell_token"], q["buy_token"])) != set(total)
            or row["phase"] not in ("FINALIZED_SUCCESS", "FINALIZED_REVERT")
            or not receipt
            or receipt.get("verified") is not True
            or receipt.get("finalized") is not True
            or receipt.get("chain_id") != 1
            or receipt.get("tx_hash") != row["tx_hash"]
            or row["tx_hash"] in seen
            or receipt.get("status") != (row["phase"] == "FINALIZED_SUCCESS")
        ):
            raise ValueError("DEX_WALLET_FLOW_UNVERIFIED")
        seen.add(row["tx_hash"])
        if previous and (
            row["nonce"] != previous[0] + 1
            or receipt["before"] != previous[1]
            or receipt.get("native_before_raw") != previous[2]
        ):
            raise ValueError("DEX_WALLET_EXTERNAL_MOVEMENT")
        if set(receipt["token_deltas"]) != set(total):
            raise ValueError("DEX_WALLET_TOKEN_SCOPE")
        for token in total:
            raw = receipt["token_deltas"][token]
            if not isinstance(raw, str) or not re.fullmatch(
                r"-?(?:0|[1-9][0-9]*)", raw
            ):
                raise ValueError("DEX_WALLET_RAW_INVALID")
            delta = int(raw)
            if int(receipt["after"][token]) - int(receipt["before"][token]) != delta:
                raise ValueError("DEX_WALLET_BALANCE_CONFLICT")
            total[token] += delta
        paid = dec(receipt["gas_paid_raw"])
        if paid <= 0 or paid != paid.to_integral_value():
            raise ValueError("DEX_GAS_INVALID")
        native_before, native_after = dec(receipt.get("native_before_raw")), dec(
            receipt.get("native_after_raw")
        )
        if (
            any(
                x < 0 or x != x.to_integral_value()
                for x in (native_before, native_after)
            )
            or native_before - native_after != paid
        ):
            raise ValueError("DEX_NATIVE_GAS_BALANCE_CONFLICT")
        gas += int(paid)
        previous = row["nonce"], receipt["after"], receipt.get("native_after_raw")
    return dict(
        asset_raw=total[p.asset],
        quote_raw=total[p.quote],
        gas_raw=gas,
        hashes=sorted(seen),
    )


class Session:
    """Required adapters: admission, wallet sender/reader, CEX, reverse quote, costs.

    admission(plan,envelope) must validate token/CEX identity, isolated flat CEX
    account, owned reverse inventory, private margin, allowance, loss/equity limits,
    reverse-route liquidity and conservative post-cost NET. No default acceptance.
    costs(plan,flow,closed_at) returns scoped mature private funding plus auditable
    gas valuation; neither public funding estimates nor fixed gas guesses suffice.
    """

    def __init__(
        self,
        path,
        sender,
        reader,
        cex,
        admission,
        reverse_quote,
        costs,
        entry_authority,
        exit_authority,
        hedge_admission=None,
        clock=time.time,
    ):
        self.store = Store(path)
        self.path, self.sender, self.reader, self.cex = str(path), sender, reader, cex
        self.admission, self.reverse_quote, self.costs = admission, reverse_quote, costs
        self.entry_authority, self.exit_authority, self.clock = (
            entry_authority,
            exit_authority,
            clock,
        )
        self.hedge_admission = hedge_admission

    async def init(self):
        await self.store.init()
        async with aiosqlite.connect(self.path) as d:
            await d.executescript("""
            CREATE TABLE IF NOT EXISTS dex_live_stages(trade_id TEXT,stage TEXT,kind TEXT,created_at REAL,payload TEXT,PRIMARY KEY(trade_id,stage));
            CREATE TABLE IF NOT EXISTS dex_live_events(id INTEGER PRIMARY KEY,trade_id TEXT,ts REAL,phase TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS live_results(trade_id TEXT PRIMARY KEY,ts REAL,gross REAL,fees REAL,funding REAL,net REAL,reason TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS ledger(ts REAL,kind TEXT,trade_id TEXT,venue TEXT,amount REAL,note TEXT);
            """)
            await d.commit()

    async def _row(self, tid):
        row = await self.store.get(tid)
        if not row:
            raise ValueError("DEX_TRADE_MISSING")
        meta = json.loads(row["payload"])
        if meta.get("strategy") != "cex_dex" or not meta.get("dex_live_plan"):
            raise ValueError("DEX_TRADE_SCOPE")
        p = Plan(**meta["dex_live_plan"])
        p.validate()
        return row, meta, p

    async def _change(self, tid, phases, target, stage=None, kind=None, **fields):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cur = await d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?", (tid,)
            )
            row = await cur.fetchone()
            if not row or row[0] not in phases:
                return False
            meta = json.loads(row[1])
            if stage:
                try:
                    await d.execute(
                        "INSERT INTO dex_live_stages VALUES(?,?,?,?,?)",
                        (tid, stage, kind, self.clock(), json.dumps(fields)),
                    )
                except aiosqlite.IntegrityError:
                    return False
            meta.update(fields)
            await d.execute(
                "UPDATE live_trades SET phase=?,payload=?,updated_at=? WHERE trade_id=?",
                (target, json.dumps(meta), self.clock(), tid),
            )
            await d.execute(
                "INSERT INTO dex_live_events(trade_id,ts,phase,payload) VALUES(?,?,?,?)",
                (tid, self.clock(), target, json.dumps(fields)),
            )
            await d.commit()
            return True

    async def enter(self, tid, p, envelope):
        if await self.store.get(tid):
            return dict(status="RECONCILE_REQUIRED", trade_id=tid)
        try:
            p.validate()
            if not isinstance(tid, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,40}", tid
            ):
                raise ValueError("DEX_TRADE_ID_INVALID")
            if self.entry_authority(p.venue) is not True:
                raise ValueError("DEX_ENTRY_AUTHORITY_REQUIRED")
            if self.sender.policy.cex_venue != p.venue:
                raise ValueError("DEX_POLICY_VENUE_MISMATCH")
            self.sender.policy.check(envelope, self.clock())
            self._quote(p, envelope, None)
            if not 0 <= self.clock() - p.opened_at <= 15:
                raise ValueError("DEX_ADMISSION_STALE")
            if await self.admission(p, envelope) is not True:
                raise ValueError("DEX_ADMISSION_REQUIRED")
        except Exception as e:
            return dict(status="BLOCKED", reason=reason(e))
        if not await self.store.reserve_entry(
            tid,
            strategy="cex_dex",
            symbol=p.symbol,
            long_venue="dex:1" if p.direction == "forward" else p.venue,
            short_venue=p.venue if p.direction == "forward" else "dex:1",
            planned_long=0,
            planned_short=0,
            dex_live_plan=asdict(p),
            wallet_address=p.wallet,
            wallet_chain=1,
            cex_venue=p.venue,
            wallet_quote_fingerprint=envelope.proof["quote_fingerprint"],
            dex_entry_edge=getattr(self.admission, "latest", {}).get("ceiling"),
        ):
            return dict(status="BLOCKED", reason="GLOBAL_LIVE_CAPACITY")
        try:
            # Wallet Journal claims PLANNED and retains the common owner on errors.
            await self.sender.send(tid, tid + ":dex:entry", envelope)
            return await self.observe(tid)
        except asyncio.CancelledError:
            raise
        except Exception:
            return dict(
                status="HOLD",
                reason="DEX_ENTRY_SEND_REQUIRES_RECONCILIATION",
                trade_id=tid,
            )

    def _quote(self, p, e, raw):
        if not isinstance(e, Envelope) or e.taker != p.wallet:
            raise ValueError("DEX_ENVELOPE_SCOPE")
        q = e.proof
        sell, buy = (
            (p.quote, p.asset) if p.direction == "forward" else (p.asset, p.quote)
        )
        if raw is not None:
            sell, buy = buy, sell
        mode = (
            "exact_in"
            if (p.direction == "forward") == (raw is not None)
            else "exact_out"
        )
        # Entry forward is exact-out; reverse entry and forward close exact-in.
        if (q["sell_token"], q["buy_token"], q["quote_mode"]) != (sell, buy, mode):
            raise ValueError("DEX_QUOTE_DIRECTION")
        if raw is not None and int(q["requested_amount_raw"]) != abs(raw):
            raise ValueError("DEX_REVERSE_AMOUNT_CHANGED")

    async def observe(self, tid):
        row, meta, p = await self._row(tid)
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            cur = await d.execute(
                "SELECT * FROM wallet_tx_intents WHERE trade_id=? ORDER BY nonce",
                (tid,),
            )
            intents = [dict(x) for x in await cur.fetchall()]
        if not intents:
            return dict(
                status="HOLD", reason="DEX_WALLET_INTENT_MISSING", phase=row["phase"]
            )
        try:
            # Revalidate final receipts as well; unavailable/reorg means HOLD.
            for intent in intents:
                r = await self.reader.reconcile(intent["intent_id"])
                if r.get("status") not in ("FINALIZED_SUCCESS", "FINALIZED_REVERT"):
                    return dict(
                        status="HOLD",
                        reason=r.get("reason", r["status"]),
                        phase=row["phase"],
                    )
                updated = await self.sender.journal.get(intent["intent_id"])
                intent.update(updated)
            flow = wallet_flow(intents, tid, p)
            inventory = await self.reader.inventory(p.wallet, (p.asset, p.quote))
            last = max(intents, key=lambda r: r["nonce"])
            receipt = json.loads(last["payload"])["receipt"]
            if (
                inventory.get("verified") is not True
                or inventory.get("wallet") != p.wallet
                or inventory.get("chain_id") != 1
                or inventory.get("nonce") != last["nonce"] + 1
                or inventory.get("balances") != receipt["after"]
                or inventory.get("native_raw") != receipt.get("native_after_raw")
                or not 0 <= self.clock() - inventory["ts"] <= 15
            ):
                raise ValueError("DEX_WALLET_CURRENT_STATE_CHANGED")
            cex = await self.cex.reconcile(tid, p)
            if (
                cex.get("verified") is not True
                or not 0 <= self.clock() - cex["ts"] <= 15
            ):
                raise ValueError("DEX_CEX_PRIVATE_UNVERIFIED")
            async with aiosqlite.connect(self.path) as d:
                cur = await d.execute(
                    "SELECT stage,payload FROM dex_live_stages WHERE trade_id=?", (tid,)
                )
                for stage, payload in await cur.fetchall():
                    claim = json.loads(payload)
                    if claim.get("expected_cex") is True and stage not in cex.get(
                        "stages", []
                    ):
                        raise ValueError("DEX_CEX_CLAIM_WITHOUT_TERMINAL_INTENT")
                    if claim.get("expected_cex") is True:
                        matches = [
                            json.loads(s[2])
                            for s in cex.get("journal_snapshot", [])
                            if s[0] == tid + ":cash:" + stage
                        ]
                        with localcontext() as ctx:
                            ctx.prec = 80
                            if len(matches) != 1 or dec(matches[0]["qty"]) * dec(
                                p.contract_size
                            ) * 10**p.asset_decimals != abs(
                                dec(claim["cex_requested_raw"])
                            ):
                                raise ValueError("DEX_CEX_CLAIM_AMOUNT_CHANGED")
            if meta.get("dex_expected_wallet_intent") and meta[
                "dex_expected_wallet_intent"
            ] not in {r["intent_id"] for r in intents}:
                raise ValueError("DEX_WALLET_CLAIM_WITHOUT_INTENT")
            with localcontext() as ctx:
                ctx.prec = 80
                base_raw = dec(cex["base"]) * 10**p.asset_decimals
                if base_raw != base_raw.to_integral_value():
                    raise ValueError("DEX_CEX_TOKEN_RESOLUTION")
                if base_raw and ((base_raw > 0) == (p.direction == "forward")):
                    raise ValueError("DEX_CEX_HEDGE_DIRECTION")
            return dict(
                status="VERIFIED",
                trade_id=tid,
                phase=row["phase"],
                wallet=flow,
                wallet_inventory=inventory,
                wallet_snapshot=sorted(
                    [r["intent_id"], r["phase"], r["tx_hash"], r["payload"]]
                    for r in intents
                ),
                cex=cex,
                mismatch_raw=flow["asset_raw"] + int(base_raw),
            )
        except Exception as e:
            return dict(status="HOLD", phase=row["phase"], reason=reason(e))

    async def hedge(self, tid):
        row, meta, p = await self._row(tid)
        if row["phase"] != "DEX_WALLET_PENDING":
            return dict(status="RECONCILE_REQUIRED")
        obs = await self.observe(tid)
        if obs["status"] != "VERIFIED":
            return obs
        if dec(obs["cex"]["base"]) != 0:
            return dict(status="HOLD", reason="DEX_ENTRY_CEX_ALREADY_PRESENT")
        asset = obs["wallet"]["asset_raw"]
        if asset == 0:
            await self._change(
                tid,
                (row["phase"],),
                "DEX_ACCOUNTING_PENDING",
                dex_closed_at=self.clock(),
            )
            return dict(status="ACCOUNTING_PENDING")
        if (asset > 0) != (p.direction == "forward"):
            return dict(status="HOLD", reason="DEX_ENTRY_INVENTORY_DIRECTION")
        if self.entry_authority(p.venue) is not True:
            return dict(
                status="RECOVERY_REQUIRED", reason="DEX_ENTRY_AUTHORITY_EXPIRED"
            )
        if callable(getattr(self.admission, "prepare_hedge", None)):
            request = await self.admission.prepare_hedge(p, obs)
            if request is None:
                return dict(
                    status="RECOVERY_REQUIRED", reason="DEX_POST_RECEIPT_NET_UNVERIFIED"
                )
        else:
            request = await self.cex.prepare(p, -asset, closing=False)
            if (
                self.hedge_admission is None
                or await self.hedge_admission(p, obs, request) is not True
            ):
                return dict(
                    status="RECOVERY_REQUIRED", reason="DEX_POST_RECEIPT_NET_UNVERIFIED"
                )
        if not await self._change(
            tid,
            (row["phase"],),
            "DEX_HEDGE_SUBMITTING",
            stage="hedge",
            kind="CEX",
            expected_cex=True,
            cex_requested_raw=str(-asset),
        ):
            return dict(status="RECONCILE_REQUIRED")
        try:
            await self.cex.send(tid, "hedge", p, request, closing=False)
            obs = await self.observe(tid)
            if obs["status"] != "VERIFIED":
                return obs
            complete = obs["mismatch_raw"] == 0
            await self._change(
                tid,
                ("DEX_HEDGE_SUBMITTING",),
                "DEX_OPEN" if complete else "DEX_RECOVERY_REQUIRED",
            )
            return dict(
                status="OPEN" if complete else "RECOVERY_REQUIRED", observation=obs
            )
        except Exception:
            return dict(status="HOLD", reason="DEX_CEX_SEND_UNKNOWN")

    async def abort_reserved(self, tid):
        """Only a never-claimed wallet/CEX reservation can be released without costs.

        A racing Sender.reserve then sees ABORTED and cannot sign/broadcast.
        SIGNING/UNKNOWN or any wallet/order journal entry prevents this path.
        """
        await self._row(tid)
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cur = await d.execute(
                "SELECT phase FROM live_trades WHERE trade_id=?", (tid,)
            )
            row = await cur.fetchone()
            if not row or row[0] != "PLANNED":
                return dict(status="RECONCILE_REQUIRED")
            for table in ("wallet_tx_intents", "order_intents", "dex_live_stages"):
                cur = await d.execute(
                    "SELECT 1 FROM " + table + " WHERE trade_id=? LIMIT 1", (tid,)
                )
                if await cur.fetchone():
                    return dict(
                        status="HOLD", reason="DEX_RESERVED_EXECUTION_EVIDENCE_PRESENT"
                    )
            await d.execute(
                "UPDATE live_trades SET phase='ABORTED',updated_at=? WHERE trade_id=?",
                (self.clock(), tid),
            )
            await d.execute(
                "INSERT INTO dex_live_events(trade_id,ts,phase,payload) VALUES(?,?,?,?)",
                (
                    tid,
                    self.clock(),
                    "ABORTED",
                    json.dumps(dict(reason="NO_EXECUTION_CLAIM_NO_SEND")),
                ),
            )
            await d.commit()
        return dict(status="ABORTED", trade_id=tid)

    async def close(self, tid, recovery=False):
        row, meta, p = await self._row(tid)
        if row["phase"] == "CLOSED_PRIVATE_VERIFIED":
            return dict(status="ALREADY_CLOSED")
        obs = await self.observe(tid)
        if obs["status"] != "VERIFIED":
            return obs
        if self.exit_authority(p.venue) is not True:
            return dict(status="HOLD", reason="DEX_EXIT_AUTHORITY_REQUIRED")
        allowed = (
            ("DEX_OPEN",)
            if not recovery
            else (
                "DEX_WALLET_PENDING",
                "DEX_RECOVERY_REQUIRED",
                "DEX_HEDGE_SUBMITTING",
                "DEX_CEX_EXIT_SUBMITTING",
                "DEX_EXIT_SUBMITTING",
                "DEX_ACCOUNTING_PENDING",
            )
        )
        if row["phase"] not in allowed:
            return dict(status="RECONCILE_REQUIRED")
        n = meta.get("dex_recovery_round", 0)
        if type(n) is not int or n < 0 or recovery and n >= 3:
            return dict(status="HOLD", reason="DEX_RECOVERY_ROUND_LIMIT")
        stage = "exit" if not recovery else "recovery-" + str(n + 1)
        target = "DEX_CEX_EXIT_SUBMITTING"
        with localcontext() as ctx:
            ctx.prec = 80
            cex_raw = -dec(obs["cex"]["base"]) * 10**p.asset_decimals
        if not await self._change(
            tid,
            (row["phase"],),
            target,
            stage=stage,
            kind="EXIT",
            dex_recovery_round=n + int(recovery),
            expected_cex=dec(obs["cex"]["base"]) != 0,
            cex_requested_raw=str(cex_raw),
        ):
            return dict(status="RECONCILE_REQUIRED")
        try:
            base = dec(obs["cex"]["base"])
            if base:
                with localcontext() as ctx:
                    ctx.prec = 80
                    raw = -base * 10**p.asset_decimals
                request = await self.cex.prepare(p, int(raw), closing=True)
                await self.cex.send(tid, stage, p, request, closing=True)
                obs = await self.observe(tid)
                if obs["status"] != "VERIFIED":
                    return obs
                if dec(obs["cex"]["base"]) != 0:
                    await self._change(tid, (target,), "DEX_RECOVERY_REQUIRED")
                    return dict(
                        status="RECOVERY_REQUIRED", reason="DEX_CEX_CLOSE_PARTIAL"
                    )
            # Never reverse the wallet while the derivative close is unresolved.
            asset = obs["wallet"]["asset_raw"]
            if asset:
                envelope = await self.reverse_quote(p, asset)
                self._quote(p, envelope, asset)
                self.sender.policy.check(envelope, self.clock())
                if self.exit_authority(p.venue) is not True:
                    raise ValueError("DEX_EXIT_AUTHORITY_EXPIRED")
                if not await self._change(
                    tid,
                    (target,),
                    "DEX_EXIT_SUBMITTING",
                    wallet_quote_fingerprint=envelope.proof["quote_fingerprint"],
                    dex_expected_wallet_intent=tid + ":dex:" + stage,
                ):
                    return dict(status="RECONCILE_REQUIRED")
                await self.sender.send(
                    tid, tid + ":dex:" + stage, envelope, closing=True
                )
                obs = await self.observe(tid)
                if obs["status"] != "VERIFIED":
                    return obs
            if obs["wallet"]["asset_raw"] or dec(obs["cex"]["base"]):
                await self._change(
                    tid, (target, "DEX_WALLET_PENDING"), "DEX_RECOVERY_REQUIRED"
                )
                return dict(status="RECOVERY_REQUIRED")
            await self._change(
                tid,
                (target, "DEX_WALLET_PENDING"),
                "DEX_ACCOUNTING_PENDING",
                dex_closed_at=self.clock(),
            )
            return dict(status="ACCOUNTING_PENDING")
        except Exception:
            # Keep claimed phase; explicit recovery requires fresh observation.
            return dict(status="HOLD", reason="DEX_EXIT_REQUIRES_RECONCILIATION")

    async def finalize(self, tid):
        row, meta, p = await self._row(tid)
        if row["phase"] == "CLOSED_PRIVATE_VERIFIED":
            return dict(status="ALREADY_CLOSED")
        if row["phase"] != "DEX_ACCOUNTING_PENDING":
            return dict(status="RECONCILE_REQUIRED")
        obs = await self.observe(tid)
        if obs["status"] != "VERIFIED":
            return obs
        if obs["wallet"]["asset_raw"] or dec(obs["cex"]["base"]):
            return dict(status="HOLD", reason="DEX_NOT_FLAT")
        try:
            costs = await self.costs(p, obs, meta["dex_closed_at"])
            if (
                costs.get("verified") is not True
                or costs.get("trade_id") != tid
                or costs.get("venue") != p.venue
                or costs.get("symbol") != p.symbol
                or costs.get("gas_raw") != str(obs["wallet"]["gas_raw"])
                or costs.get("hashes") != obs["wallet"]["hashes"]
                or dec(costs.get("funding_covered_until")) < dec(meta["dex_closed_at"])
                or not costs.get("gas_valuation_evidence")
                or not costs.get("quote_usdt_identity_evidence")
            ):
                raise ValueError("DEX_FINAL_COSTS_UNVERIFIED")
            with localcontext() as ctx:
                ctx.prec = 80
                quote = dec(obs["wallet"]["quote_raw"]) / 10**p.quote_decimals
                gross = quote + dec(obs["cex"]["realized"])
                gas, fees, funding = (
                    dec(costs["gas_usdt"]),
                    dec(obs["cex"]["fees"]),
                    dec(costs["funding"]),
                )
                if gas < 0 or fees < 0 or obs["wallet"]["gas_raw"] and gas <= 0:
                    raise ValueError("DEX_COST_SIGN_INVALID")
                net = gross - gas - fees + funding - dec(p.safety)
        except Exception as e:
            return dict(status="ACCOUNTING_PENDING", reason=reason(e))
        refreshed = await self.observe(tid)
        if refreshed.get("status") != "VERIFIED":
            return refreshed
        if refreshed["wallet_snapshot"] != obs["wallet_snapshot"] or refreshed[
            "cex"
        ].get("journal_snapshot") != obs["cex"].get("journal_snapshot"):
            return dict(status="ACCOUNTING_PENDING", reason="DEX_FINAL_PROOF_CHANGED")
        obs = refreshed
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            cur = await d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?", (tid,)
            )
            fresh = await cur.fetchone()
            if not fresh or fresh[0] != row["phase"] or fresh[1] != row["payload"]:
                return dict(status="RECONCILE_REQUIRED")
            # Freeze both durable journals inside the same result transaction.
            cur = await d.execute(
                "SELECT intent_id,phase,tx_hash,payload FROM wallet_tx_intents WHERE trade_id=?",
                (tid,),
            )
            if (
                sorted([list(r) for r in await cur.fetchall()])
                != obs["wallet_snapshot"]
            ):
                return dict(
                    status="ACCOUNTING_PENDING", reason="DEX_WALLET_PROOF_CHANGED"
                )
            from .live_cash_dex_attribution import intents as validated_intents

            try:
                _, intents = await validated_intents(d, tid, allow_empty=True)
            except (ValueError, TypeError, KeyError):
                return dict(
                    status="ACCOUNTING_PENDING",
                    reason="DEX_CEX_INTENT_COLUMNS_CONFLICT",
                )
            from .dex_cex_backend import rebuild, snapshot

            if snapshot(intents) != obs["cex"].get("journal_snapshot"):
                return dict(status="ACCOUNTING_PENDING", reason="DEX_CEX_PROOF_CHANGED")
            flow = rebuild(intents, tid, p)
            if any(
                flow[k] != obs["cex"][k] for k in ("base", "realized", "fees", "stages")
            ):
                return dict(status="ACCOUNTING_PENDING", reason="DEX_CEX_FLOW_CHANGED")
            result = dict(
                observation=obs,
                costs=costs,
                plan=asdict(p),
                gross=str(gross),
                fees=str(fees),
                gas=str(gas),
                funding=str(funding),
                net=str(net),
            )
            await d.execute(
                "INSERT INTO live_results VALUES(?,?,?,?,?,?,?,?)",
                (
                    tid,
                    self.clock(),
                    float(gross),
                    float(fees + gas),
                    float(funding),
                    float(net),
                    "DEX_BOTH_SIDES_PRIVATE_VERIFIED",
                    json.dumps(result),
                ),
            )
            await d.execute(
                "INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                (
                    self.clock(),
                    "LIVE_NET",
                    tid,
                    p.venue,
                    float(net),
                    "DEX_PRIVATE_FINAL",
                ),
            )
            meta.update(dex_result=result)
            await d.execute(
                "INSERT INTO dex_live_events(trade_id,ts,phase,payload) VALUES(?,?,?,?)",
                (tid, self.clock(), "CLOSED_PRIVATE_VERIFIED", json.dumps(result)),
            )
            await d.execute(
                "UPDATE live_trades SET phase='CLOSED_PRIVATE_VERIFIED',payload=?,updated_at=? WHERE trade_id=?",
                (json.dumps(meta), self.clock(), tid),
            )
            await d.commit()
        return dict(status="CLOSED", trade_id=tid, net=str(net))
