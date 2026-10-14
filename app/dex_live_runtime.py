"""Configured write coordinator. All writes use the durable Session boundaries.

Quote calldata stays in an ephemeral single-use candidate cache. Restart never
replays an entry: verified old exposure may only take a newly claimed exit.
"""

import asyncio
import copy
import json
import math
import time
import uuid
from decimal import localcontext
from .dex_firm_simulation import Envelope, registry_scope, integer
from .dex_live_bridge import Plan, dec, reason
from .dex_live_costs import USDT_MAINNET
from .native_order_plan import market
from .dynamic_exit import ExitState, decide
from .private_funding_reader import Reader as FundingReader


class Source:
    def __init__(
        self,
        provider,
        registry,
        policy,
        clients,
        routes,
        admission,
        budget=5,
        safety="0.005",
        clock=time.time,
        entry_gate=lambda venue: False,
    ):
        self.provider, self.registry, self.policy, self.clients = (
            provider,
            registry,
            policy,
            clients,
        )
        self.routes, self.admission, self.budget, self.safety = (
            routes,
            admission,
            budget,
            safety,
        )
        self.clock, self.index, self.cache = clock, 0, {}
        self.entry_gate = entry_gate
        self.entry_enabled = True
        if not isinstance(routes, list) or not routes:
            raise ValueError("DEX_LIVE_ROUTES_REQUIRED")
        for r in routes:
            if (
                not isinstance(r, dict)
                or set(r) != {"asset_token", "asset_amount_raw", "direction"}
                or r["direction"] not in ("forward", "reverse")
            ):
                raise ValueError("DEX_LIVE_ROUTE_INVALID")
            integer(r["asset_amount_raw"], "DEX_ASSET_AMOUNT")

    async def reverse(self, p, raw):
        registry = self.registry()
        sell, buy = (p.asset, p.quote) if raw > 0 else (p.quote, p.asset)
        e = await self.provider.firm(
            1,
            sell,
            buy,
            str(abs(raw)),
            p.wallet,
            registry,
            exact_out=raw < 0,
            execution_envelope=True,
        )
        if not isinstance(e, Envelope):
            raise ValueError("DEX_REVERSE_FIRM_UNAVAILABLE")
        return e

    async def cycle(self):
        self.cache.clear()
        if not self.entry_enabled or self.entry_gate(self.policy.cex_venue) is not True:
            return []
        route = self.routes[self.index % len(self.routes)]
        self.index += 1
        try:
            registry = self.registry()
            _, tokens, asset, quote = registry_scope(
                registry, 1, route["asset_token"], USDT_MAINNET, self.clock()
            )
            identity = tokens[asset]
            venue, symbol = identity["cex_venue"], identity["cex_symbol"]
            if venue != self.policy.cex_venue:
                raise ValueError("DEX_LIVE_ROUTE_POLICY_VENUE")
            m = market(self.clients[venue], symbol)
            p = Plan(
                venue,
                symbol,
                self.policy.wallet,
                asset,
                quote,
                tokens[asset]["decimals"],
                tokens[quote]["decimals"],
                str(m["contractSize"]),
                route["direction"],
                self.clock(),
                str(self.budget),
                str(self.safety),
            )
            forward = p.direction == "forward"
            e = await self.provider.firm(
                1,
                p.quote if forward else p.asset,
                p.asset if forward else p.quote,
                str(route["asset_amount_raw"]),
                p.wallet,
                registry,
                exact_out=forward,
                execution_envelope=True,
            )
            if not isinstance(e, Envelope):
                raise ValueError("DEX_ENTRY_FIRM_UNAVAILABLE")
            if await self.admission(p, e) is not True:
                raise ValueError("DEX_LIVE_ADMISSION_REQUIRED")
            key = uuid.uuid4().hex
            self.cache[key] = (p, copy.deepcopy(e))
            return [
                dict(
                    strategy="cex_dex",
                    symbol=symbol,
                    candidate_id=key,
                    net=float(self.admission.latest["ceiling"]),
                    evidence=copy.deepcopy(self.admission.latest),
                    ts=self.clock(),
                    reason="DEX_LIVE_CANDIDATE_REQUIRES_FRESH_SEND_GATES",
                )
            ]
        except Exception as error:
            return [
                dict(
                    strategy="cex_dex",
                    symbol=route["asset_token"],
                    reason=reason(error),
                    status="BLOCKED",
                    ts=self.clock(),
                )
            ]

    def take(self, key):
        result = self.cache.pop(key, None)
        if not result or not 0 <= self.clock() - result[0].opened_at <= 15:
            raise ValueError("DEX_CANDIDATE_STALE_OR_CONSUMED")
        return result


class Marker:
    def __init__(
        self,
        session,
        admission,
        funding_service,
        max_seconds=1200,
        target=0.7,
        trailing=0.2,
        stop_net=-0.5,
        pending_seconds=900,
    ):
        self.session, self.admission, self.funding = session, admission, funding_service
        self.clock = session.clock
        self.pending_seconds = dec(pending_seconds)
        if not 0 < self.pending_seconds <= 3600:
            raise ValueError("DEX_PENDING_TIME_LIMIT_INVALID")
        self.max_seconds, self.target, self.trailing, self.stop = (
            max_seconds,
            target,
            trailing,
            stop_net,
        )
        for x in (max_seconds, target, trailing, stop_net):
            if isinstance(x, bool) or not math.isfinite(float(x)):
                raise ValueError("DEX_EXIT_CONFIG_INVALID")
        if (
            not 0 < max_seconds <= 3600
            or not 0 < target <= 1
            or not 0 < trailing < 1
            or stop_net >= 0
        ):
            raise ValueError("DEX_EXIT_CONFIG_INVALID")

    async def coverage(self, p, now):
        reader = FundingReader(
            p.venue, self.session.cex.clients[p.venue], clock=self.clock
        )
        cutoff = now - reader.maturity
        if cutoff < p.opened_at:
            return dict(
                verified=False, amount=0, reason="FUNDING_ENTRY_MATURITY_PENDING"
            )
        evidence = await reader.collect(p.symbol, p.opened_at, cutoff)
        f = await self.funding.get(p.venue, p.symbol)
        next_ts, interval = dec(f.next_ts) / 1000, dec(f.interval_hours) * 3600
        verified = (
            evidence.verified
            and evidence.covered_until >= cutoff
            and (f.exchange, f.symbol) == (p.venue, p.symbol)
            and 0 < interval <= 86400
            and dec(now) < next_ts <= dec(now) + interval
            and next_ts - interval <= dec(cutoff)
        )
        return dict(
            verified=verified,
            amount=evidence.amount,
            events=list(evidence.events),
            reason=(
                "MATURE_PRIVATE_INCOME_CALENDAR" if verified else "FUNDING_CALENDAR_GAP"
            ),
        )

    async def observe(self, row):
        tid = row["trade_id"]
        obs = await self.session.observe(tid)
        info = dict(
            trade_id=tid,
            strategy="cex_dex",
            symbol=row["symbol"],
            phase=row["phase"],
            long_venue=row["long_venue"],
            short_venue=row["short_venue"],
            private_verified=obs["status"] == "VERIFIED",
            dex_observation=obs,
            exit_signal="HOLD",
            cash_error=obs.get("reason"),
        )
        if not info["private_verified"] or row["phase"] != "DEX_OPEN":
            if (
                row["phase"] == "DEX_WALLET_PENDING"
                and obs.get("reason") == "WALLET_RECEIPT_PENDING"
            ):
                import aiosqlite

                async with aiosqlite.connect(self.session.path) as d:
                    cur = await d.execute(
                        "SELECT phase,tx_hash,created_at FROM wallet_tx_intents WHERE trade_id=? ORDER BY nonce DESC LIMIT 1",
                        (tid,),
                    )
                    intent = await cur.fetchone()
                info["settlement_pending"] = bool(
                    intent
                    and intent[0] == "PENDING"
                    and intent[1]
                    and 0 <= self.clock() - intent[2] <= self.pending_seconds
                )
            return info
        if obs["mismatch_raw"] != 0:
            return dict(
                info, private_verified=False, cash_error="DEX_OPEN_HEDGE_MISMATCH"
            )
        _, meta, p = await self.session._row(tid)
        if self.clock() - p.opened_at >= self.max_seconds:
            info["exit_signal"] = "TIME_STOP"
        try:
            asset = obs["wallet"]["asset_raw"]
            e = await self.session.reverse_quote(p, asset)
            self.session._quote(p, e, asset)
            self.session.sender.policy.check(e, self.clock())
            client = self.session.cex.clients[p.venue]
            fee = await asyncio.wait_for(client.fetch_trading_fee(p.symbol), 8)
            if fee.get("symbol") != p.symbol:
                raise ValueError("DEX_EXIT_FEE_SCOPE")
            from .spot_future_native_plan import rate

            fee_rate = rate(fee.get("taker"))
            try:
                funding = await self.coverage(p, self.clock())
            except Exception:
                funding = dict(
                    verified=False, amount=0, reason="FUNDING_EVIDENCE_UNAVAILABLE"
                )
            gas, gas_book = await self.admission.gas(
                obs["wallet"]["gas_raw"] + int(e.proof["network_fee_raw"])
            )
            with localcontext() as ctx:
                ctx.prec = 80
                raw = -dec(obs["cex"]["base"]) * 10**p.asset_decimals
                r = await self.session.cex.prepare(p, int(raw), closing=True)
                quote = (
                    int(e.proof["min_buy_amount_raw"])
                    if asset > 0
                    else -int(e.proof["max_sell_amount_raw"])
                )
                wallet_cash = (
                    dec(obs["wallet"]["quote_raw"]) + dec(quote)
                ) / 10**p.quote_decimals
                base, entry = abs(dec(obs["cex"]["base"])), dec(
                    obs["cex"]["entry_price"]
                )
                gross = (
                    wallet_cash
                    + dec(obs["cex"]["realized"])
                    + base * (entry - dec(r.reference_price)) * (1 if asset > 0 else -1)
                )
            future_fee = base * dec(r.reference_price) * fee_rate
            price_net = (
                gross - dec(obs["cex"]["fees"]) - future_fee - gas - dec(p.safety)
            )
            net = price_net + dec(funding["amount"]) if funding["verified"] else None
            stamp = min(r.market_evidence["book_ts"], gas_book["timestamp"] / 1000)
            if (
                not 0 <= self.clock() - stamp <= 1.5
                or not 0 <= self.clock() - e.proof["ts"] <= 15
            ):
                raise ValueError("DEX_PAIRED_EXIT_EVIDENCE_STALE")
            self.session.sender.policy.check(e, self.clock())
            edge = dec(meta.get("dex_entry_edge"))
            best = float(meta.get("dex_best_net", -1e18))
            state = ExitState(float(edge), p.opened_at, best)
            if net is not None:
                decision = decide(
                    state,
                    self.clock(),
                    float(net),
                    self.target,
                    self.trailing,
                    self.max_seconds,
                    self.stop,
                )
                info["exit_signal"] = decision.reason if decision.close else "HOLD"
            # No assumed funding credit can suppress a price/fee loss stop.
            if price_net <= dec(self.stop):
                info["exit_signal"] = "NET_STOP"
            info.update(
                estimated_net=float(net) if net is not None else None,
                gross=float(gross),
                exit_fee=float(future_fee + gas),
                funding=funding["amount"],
                funding_known=funding["verified"],
                fees_verified=True,
                market_ts=stamp,
                best_net=state.best_net,
                dex_mark_proof=dict(
                    wallet_quote=e.proof,
                    cex=r.market_evidence,
                    gas=gas_book,
                    funding=funding,
                ),
                reason="EXECUTABLE_SEQUENTIAL_EXIT_ESTIMATE_NOT_REALIZED",
            )
            await self.session._change(
                tid,
                ("DEX_OPEN",),
                "DEX_OPEN",
                dex_best_net=state.best_net,
                dex_last_mark=info,
            )
        except Exception as error:
            info["cash_market_error"] = reason(error)
        return info


class Coordinator:
    def __init__(self, session, source, marker, max_unhedged_seconds=900):
        self.session, self.source, self.marker = session, source, marker
        self.clock, self.created, self.latest = (
            session.clock,
            set(),
            dict(status="NOT_STARTED"),
        )
        self.max_unhedged = dec(max_unhedged_seconds)
        if not 0 < self.max_unhedged <= 3600:
            raise ValueError("DEX_UNHEDGED_TIME_LIMIT_INVALID")

    async def process_rows(self, rows):
        if await self.session.store.active():
            return dict(status="GLOBAL_LIVE_CAPACITY")
        for row in rows:
            if not row.get("candidate_id"):
                continue
            try:
                p, e = self.source.take(row["candidate_id"])
                tid = "dx-" + uuid.uuid4().hex[:16]
                self.created.add(tid)
                self.latest = await self.session.enter(tid, p, e)
                current = await self.session.store.get(tid)
                if current:
                    await self.session._change(
                        tid,
                        (current["phase"],),
                        current["phase"],
                        dex_entry_edge=str(self.source.admission.latest["ceiling"]),
                    )
                    if current["phase"] == "PLANNED":
                        self.latest = await self.session.abort_reserved(tid)
                        self.created.discard(tid)
                else:
                    self.created.discard(tid)
                return self.latest
            except Exception as error:
                self.latest = dict(status="BLOCKED", reason=reason(error))
        return self.latest

    async def process(self, summary):
        closed = []
        if (
            not summary.get("reconciled")
            or not summary.get("private_verified")
            or summary.get("unknown_orders")
            or not 0 <= self.clock() - summary.get("ts", 0) <= 15
        ):
            return closed
        for info in summary.get("trades", []):
            if info.get("strategy") != "cex_dex" or not info.get("private_verified"):
                continue
            tid = info["trade_id"]
            row, meta, p = await self.session._row(tid)
            obs = await self.session.observe(tid)
            if obs["status"] != "VERIFIED":
                continue
            phase = row["phase"]
            if phase == "DEX_WALLET_PENDING":
                exit_started = bool(meta.get("dex_expected_wallet_intent"))
                if (
                    not exit_started
                    and not obs["cex"]["stages"]
                    and tid in self.created
                    and self.clock() - p.opened_at <= self.max_unhedged
                ):
                    try:
                        self.latest = await self.session.hedge(tid)
                    except Exception as error:
                        self.latest = dict(
                            status="RECOVERY_REQUIRED", reason=reason(error)
                        )
                    if self.latest["status"] == "RECOVERY_REQUIRED":
                        self.latest = await self.session.close(tid, recovery=True)
                else:
                    # Old/restarted entries never acquire a new speculative hedge.
                    self.latest = await self.session.close(tid, recovery=True)
            elif (
                phase == "DEX_HEDGE_SUBMITTING"
                and obs["cex"]["stages"]
                and obs["mismatch_raw"] == 0
            ):
                await self.session._change(tid, (phase,), "DEX_OPEN")
            elif phase in (
                "DEX_HEDGE_SUBMITTING",
                "DEX_RECOVERY_REQUIRED",
                "DEX_CEX_EXIT_SUBMITTING",
                "DEX_EXIT_SUBMITTING",
            ):
                self.latest = await self.session.close(tid, recovery=True)
            elif phase == "DEX_OPEN" and info.get("exit_signal") in (
                "TARGET_CAPTURE",
                "NET_TRAILING",
                "NET_STOP",
                "TIME_STOP",
            ):
                # Profit decisions must still hold against a fresh paired mark.
                fresh = await self.marker.observe(row)
                if fresh.get("private_verified") and fresh.get("exit_signal") in (
                    "TARGET_CAPTURE",
                    "NET_TRAILING",
                    "NET_STOP",
                    "TIME_STOP",
                ):
                    self.latest = await self.session.close(tid)
            elif phase == "DEX_ACCOUNTING_PENDING":
                self.latest = await self.session.finalize(tid)
                if self.latest["status"] == "CLOSED":
                    closed.append(dict(self.latest, net=float(self.latest["net"])))
                    self.created.discard(tid)
        return closed
