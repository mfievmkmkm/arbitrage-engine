"""Counterfactual round trips from chain-bound quotes; no wallet/exchange writes."""

import asyncio
import math
import time
from dataclasses import replace
from decimal import Decimal
from .dex_firm_simulation import registry_scope, integer
from .funding_paper_source import History
from .exchange_executor import SubmitRequest
from .native_order_plan import market
from .recovery_market import Reader
from .public_books import normalize
from .spot_future_native_plan import rate
from .spot_future_live_preflight import quote as entry_quote
from .native_order_plan import number


def calendar(p, until):
    opened, first, period = p["opened_at"], p["funding_next"], p["funding_period"]
    if (
        not all(math.isfinite(x) for x in (opened, first, period, until))
        or first <= opened
        or not 0 < period <= 86400
        or until < opened
    ):
        raise ValueError("DEX_FUNDING_CALENDAR_INVALID")
    count = max(0, int((until - first) // period) + 1)
    if count > 100:
        raise ValueError("DEX_FUNDING_HISTORY_WINDOW_TOO_LARGE")
    return [first + n * period for n in range(count)]


class Source:
    def __init__(self, cycle, funding_service, clock=time.time):
        self.cycle, self.fs, self.clock = cycle, funding_service, clock
        self.reader = Reader(cycle.public, clock=clock)

    def route(self, p):
        # Taker and credentials never enter the persisted position or mark payload.
        key = (
            p["chain_id"],
            p["entry_sell_token"],
            p["entry_buy_token"],
            p["route_amount_raw"],
        )
        matches = [
            r
            for r in self.cycle.routes
            if (
                int(r["chain_id"]),
                r["sell_token"].lower(),
                r["buy_token"].lower(),
                str(r["sell_amount_raw"]),
            )
            == key
        ]
        if len(matches) != 1:
            raise ValueError("DEX_PAPER_ROUTE_CHANGED_OR_AMBIGUOUS")
        return matches[0]

    async def entry(self, route):
        try:
            scope, tokens, asset, stable = registry_scope(
                self.cycle.registry,
                int(route["chain_id"]),
                route["sell_token"].lower(),
                route["buy_token"].lower(),
                self.clock(),
            )
            identity = tokens[asset]
            if self.fs is None:
                raise ValueError("DEX_FUNDING_CALENDAR_UNKNOWN")
            snapshot = await self.fs.get(identity["cex_venue"], identity["cex_symbol"])
            if snapshot.next_ts is None or snapshot.interval_hours is None:
                raise ValueError("DEX_FUNDING_CALENDAR_UNKNOWN")
            first, period = (
                float(snapshot.next_ts) / 1000,
                float(snapshot.interval_hours) * 3600,
            )
            x = await self.cycle.observe(route)
            if not x.get("simulation_allowed"):
                return dict(
                    ok=False,
                    reason=x.get(
                        "reason",
                        x.get("quote", {}).get("reason", "DEX_ENTRY_UNAVAILABLE"),
                    ),
                )
            q = x["quote"]
            p = {
                k: x[k]
                for k in (
                    "base_qty",
                    "cex_venue",
                    "cex_symbol",
                    "cex_side",
                    "cex_contracts",
                    "contract_size",
                    "entry_price",
                    "fee_rate",
                    "forward",
                    "asset_amount_raw",
                    "asset_decimals",
                    "stable_decimals",
                )
            }
            p.update(
                symbol=x["cex_symbol"],
                chain_id=q["chain_id"],
                asset=asset,
                stable=stable,
                entry_sell_token=q["sell_token"],
                entry_buy_token=q["buy_token"],
                route_amount_raw=str(route["sell_amount_raw"]),
                entry_dex=q,
                entry_cex=x["cex_evidence"],
                entry_cash=x["dex_cash"],
                entry_gas=x["gas_usd"],
                entry_gas_price=x["gas_price"],
                entry_gas_book=x["gas_book"],
                native_decimals=x["native_decimals"],
                entry_fees=x["notional"] * x["fee_rate"] + x["gas_usd"],
                entry_fee_pct=x["fee_rate"] * 100,
                safety=max(x["notional"], x["dex_cash"]) * 0.001,
                entry_edge=x["net_ceiling_model"],
                funding_next=first,
                funding_period=period,
                opened_at=self.clock(),
                reserve=12,
                model="DEX_FIRM_PUBLIC_HISTORY_MODEL",
            )
            calendar(p, p["opened_at"])
            # Both directions must simulate against existing wallet inventory before entry.
            exit_quote = await self.exit(p)
            if not exit_quote["ok"]:
                return exit_quote

            # Reverse RPC simulation can take longer than the CEX book validity.
            # Refresh both entry books afterwards without changing the DEX raw quantity.
            async def gas_book():
                start = self.clock()
                raw = await asyncio.wait_for(
                    self.cycle.public[p["cex_venue"]].fetch_order_book(
                        scope["native_symbol"], limit=20
                    ),
                    8,
                )
                return normalize(raw, scope["native_symbol"], start, self.clock(), 1.5)

            request = SubmitRequest(
                p["cex_symbol"],
                p["cex_side"],
                p["cex_contracts"],
                "limit",
                p["entry_price"],
                False,
                True,
            )
            native, gas = await asyncio.gather(
                entry_quote(
                    self.cycle.public[p["cex_venue"]],
                    p["cex_venue"],
                    request,
                    p["contract_size"],
                    clock=self.clock,
                ),
                gas_book(),
            )
            gas_price = float(number(gas["asks"][0][0], "DEX_NATIVE_PRICE"))
            gas_usd = float(
                Decimal(q["network_fee_raw"])
                / Decimal(10 ** p["native_decimals"])
                * Decimal(str(gas_price))
            )
            if not math.isfinite(gas_usd) or gas_usd > 0.25:
                raise ValueError("DEX_GAS_BUDGET_LIMIT")
            notional = p["base_qty"] * native.price
            raw_base = float(
                Decimal(p["asset_amount_raw"]) / Decimal(10 ** p["asset_decimals"])
            )
            if (
                max(notional, p["entry_cash"]) > 5
                or abs(raw_base - p["base_qty"]) * native.price > 0.05
            ):
                raise ValueError("DEX_MICRO_NOTIONAL_OR_RESIDUAL_LIMIT")
            safety = max(notional, p["entry_cash"]) * 0.001
            p.update(
                entry_price=native.price,
                entry_cex=native.market_evidence,
                entry_gas=gas_usd,
                entry_gas_price=gas_price,
                entry_gas_book=gas,
                entry_fees=notional * p["fee_rate"] + gas_usd,
                safety=safety,
                entry_edge=(
                    notional - p["entry_cash"]
                    if p["forward"]
                    else p["entry_cash"] - notional
                )
                - notional * (p["fee_rate"] + exit_quote["fee_rate"])
                - gas_usd
                - exit_quote["gas"]
                - safety,
            )
            if max(exit_quote["cash"], p["base_qty"] * exit_quote["price"]) > 5:
                raise ValueError("DEX_REVERSE_PREFLIGHT_MICRO_LIMIT")
            immediate_gross = (1 if p["forward"] else -1) * (
                exit_quote["cash"] - p["entry_cash"]
            ) + (1 if p["cex_side"] == "buy" else -1) * p["base_qty"] * (
                exit_quote["price"] - p["entry_price"]
            )
            immediate_net = (
                immediate_gross
                - p["entry_fees"]
                - p["base_qty"] * exit_quote["price"] * exit_quote["fee_rate"]
                - exit_quote["gas"]
                - p["safety"]
            )
            if immediate_net <= -max(notional, p["entry_cash"]) * 0.02:
                raise ValueError("DEX_REVERSE_PREFLIGHT_NET_STOP")
            if (
                self.clock() - q["ts"] > 15
                or self.clock() - p["entry_cex"]["book_ts"] > 1.5
                or self.clock() - p["entry_gas_book"]["timestamp"] / 1000 > 1.5
                or self.clock() - exit_quote["cex"]["book_ts"] > 1.5
                or self.clock() - exit_quote["gas_book"]["timestamp"] / 1000 > 1.5
                or self.clock() - exit_quote["dex"]["ts"] > 15
            ):
                raise ValueError("DEX_ENTRY_STALE_AFTER_REVERSE_PREFLIGHT")
            p["opened_at"] = self.clock()
            calendar(p, p["opened_at"])
            return dict(ok=True, position=p, exit_quote=exit_quote)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return dict(
                ok=False,
                reason=str(e) if isinstance(e, ValueError) else "DEX_ENTRY_UNAVAILABLE",
            )

    async def exit(self, p):
        try:
            route = self.route(p)
            scope, tokens, asset, stable = registry_scope(
                self.cycle.registry,
                p["chain_id"],
                p["entry_sell_token"],
                p["entry_buy_token"],
                self.clock(),
            )
            identity = tokens[asset]
            if (
                scope["native_symbol"] != p["entry_gas_book"]["symbol"]
                or scope["native_decimals"] != p["native_decimals"]
            ):
                raise ValueError("DEX_NATIVE_GAS_IDENTITY_CHANGED")
            if (
                asset,
                stable,
                identity["cex_venue"],
                identity["cex_symbol"],
                tokens[asset]["decimals"],
                tokens[stable]["decimals"],
            ) != (
                p["asset"],
                p["stable"],
                p["cex_venue"],
                p["cex_symbol"],
                p["asset_decimals"],
                p["stable_decimals"],
            ):
                raise ValueError("DEX_POSITION_IDENTITY_CHANGED")
            c = self.cycle.public[p["cex_venue"]]
            m = market(c, p["cex_symbol"])
            if (
                m["base"] != identity["base"]
                or float(m["contractSize"]) != p["contract_size"]
            ):
                raise ValueError("DEX_POSITION_CONTRACT_CHANGED")
            account = self.cycle.private.get(p["cex_venue"])
            if account is None or account.has.get("fetchTradingFee") is not True:
                raise ValueError("DEX_CEX_ACCOUNT_FEE_UNKNOWN")
            fee = await asyncio.wait_for(account.fetch_trading_fee(p["cex_symbol"]), 8)
            if fee.get("symbol") != p["cex_symbol"]:
                raise ValueError("DEX_CEX_FEE_SCOPE_MISMATCH")
            fee = float(rate(fee.get("taker")))
            q = await self.cycle.provider.firm(
                p["chain_id"],
                p["asset"] if p["forward"] else p["stable"],
                p["stable"] if p["forward"] else p["asset"],
                p["asset_amount_raw"],
                route["taker"],
                self.cycle.registry,
                exact_out=not p["forward"],
            )
            if not q.get("ok"):
                return dict(ok=False, reason=q.get("reason", "DEX_EXIT_UNAVAILABLE"))
            if p["forward"]:
                if (
                    q["sell_amount_raw"] != p["asset_amount_raw"]
                    or q.get("quote_mode") != "exact_in"
                ):
                    raise ValueError("DEX_EXIT_QUANTITY_CONFLICT")
                cash_raw = q["min_buy_amount_raw"]
            else:
                if (
                    q["buy_amount_raw"] != p["asset_amount_raw"]
                    or q.get("quote_mode") != "exact_out"
                ):
                    raise ValueError("DEX_EXIT_QUANTITY_CONFLICT")
                cash_raw = q["max_sell_amount_raw"]
            start = self.clock()
            raw = await asyncio.wait_for(
                c.fetch_order_book(scope["native_symbol"], limit=20), 8
            )
            b = normalize(raw, scope["native_symbol"], start, self.clock(), 1.5)
            decimals = integer(scope["native_decimals"], "DEX_NATIVE_DECIMALS", False)
            if decimals > 36:
                raise ValueError("DEX_NATIVE_DECIMALS_INVALID")
            gas = float(
                Decimal(q["network_fee_raw"])
                / Decimal(10**decimals)
                * Decimal(str(b["asks"][0][0]))
            )
            if not math.isfinite(gas) or not 0 <= gas <= 0.25:
                raise ValueError("DEX_GAS_BUDGET_LIMIT")
            request = SubmitRequest(
                p["cex_symbol"],
                "buy" if p["cex_side"] == "sell" else "sell",
                p["cex_contracts"],
                "market",
                reduce_only=True,
            )
            native = await self.reader.quote(
                p["cex_venue"], request, p["contract_size"]
            )
            now = self.clock()
            if (
                not 0 <= now - q["ts"] <= 15
                or not 0 <= now - b["timestamp"] / 1000 <= 1.5
            ):
                raise ValueError("DEX_EXIT_PAIRED_EVIDENCE_STALE")
            cash = float(Decimal(cash_raw) / Decimal(10 ** p["stable_decimals"]))
            return dict(
                ok=True,
                ts=now,
                dex=q,
                cex=native.market_evidence,
                price=native.reference_price,
                cash=cash,
                gas=gas,
                gas_price=float(b["asks"][0][0]),
                gas_book=b,
                fee_rate=fee,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return dict(
                ok=False,
                reason=str(e) if isinstance(e, ValueError) else "DEX_EXIT_UNAVAILABLE",
            )

    async def settlements(self, p, until):
        try:
            expected = calendar(p, until)
            if not expected:
                return History(True, 0, (), "NO_SETTLEMENT_DUE", until)
            c = self.cycle.public[p["cex_venue"]]
            if c.has.get("fetchFundingRateHistory") is not True:
                raise ValueError("FUNDING_PUBLIC_HISTORY_UNSUPPORTED")
            rows = await asyncio.wait_for(
                c.fetch_funding_rate_history(
                    p["cex_symbol"], int(p["opened_at"] * 1000), 100
                ),
                8,
            )
            if not isinstance(rows, list) or len(rows) >= 100:
                raise ValueError("FUNDING_HISTORY_TRUNCATED")
            actual = {}
            for row in rows:
                if row.get("symbol") != p["cex_symbol"]:
                    continue
                ts, r = float(row["timestamp"]) / 1000, float(row["fundingRate"])
                if not math.isfinite(ts) or not math.isfinite(r) or abs(r) >= 1:
                    raise ValueError("FUNDING_HISTORY_INVALID")
                if not p["opened_at"] < ts <= until:
                    continue
                matching = [s for s in expected if abs(s - ts) <= 1]
                if len(matching) != 1:
                    raise ValueError("FUNDING_CALENDAR_CHANGED")
                ts = matching[0]
                if ts in actual and actual[ts] != r:
                    raise ValueError("FUNDING_HISTORY_CONFLICT")
                actual[ts] = r
            if set(actual) != set(expected):
                raise ValueError("FUNDING_SETTLEMENT_MISSING")
            if until > self.clock() or max(expected) > self.clock() - 30:
                raise ValueError("FUNDING_HISTORY_MATURITY_PENDING")
            sign = 1 if p["cex_side"] == "sell" else -1
            events = tuple(
                dict(
                    venue=p["cex_venue"],
                    ts=s,
                    rate=r,
                    amount=sign * r * p["base_qty"] * p["entry_price"],
                    mode="PUBLIC_HISTORY_ENTRY_REFERENCE_MODEL",
                )
                for s, r in sorted(actual.items())
            )
            return History(
                True,
                sum(e["amount"] for e in events),
                events,
                "VERIFIED_MODEL_HISTORY",
                until,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return History(
                False,
                0,
                (),
                str(e) if isinstance(e, ValueError) else "FUNDING_HISTORY_UNAVAILABLE",
            )

    def extend_history(self, p, h, until):
        if h.verified and calendar(p, h.covered_until) == calendar(p, until):
            return replace(h, covered_until=until)
        return History(
            False, h.amount, h.events, "FUNDING_NOT_COVERED_AT_QUOTE", h.covered_until
        )
