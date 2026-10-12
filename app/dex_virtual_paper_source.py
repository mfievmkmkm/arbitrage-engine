"""Public-price virtual experiments. No taker, RPC, signer or order submission.

Paired mode buys virtual DEX inventory and shorts CEX. Reference mode holds
only a directional CEX future. Indicative prices are never firm evidence.
"""

import asyncio
import math
import time
from decimal import Decimal
from types import SimpleNamespace

from .cex_dex_paper import mark as paired_mark
from .cex_dex_paper_source import Source as HistorySource, calendar
from .exchange_executor import SubmitRequest
from .native_order_plan import market, number, format_value, quantum
from .public_books import normalize
from .recovery_market import Reader
from .spot_future_native_plan import rate
from .zerox_research import ADDRESS
from .jupiter_research import mint_valid
from .dex_signal_focus import enabled, quality, entry_venue_allowed


def token_key(r, token):
    return token if r["chain_id"] == "solana" else token.lower()


def routes_checked(routes):
    if not isinstance(routes, list) or not routes or len(routes) > 32:
        raise ValueError("DEX_VIRTUAL_ROUTES_REQUIRED")
    identities = set()
    for r in routes:
        required = {"chain_id", "stable", "asset", "stable_decimals", "asset_decimals",
                    "base", "cex_venue", "cex_symbol", "native_symbol", "native_decimals"}
        if not isinstance(r, dict) or not required <= r.keys():
            raise ValueError("DEX_VIRTUAL_ROUTE_INVALID")
        solana = r["chain_id"] == "solana"
        if not solana and (type(r["chain_id"]) is not int or r["chain_id"] <= 0):
            raise ValueError("DEX_VIRTUAL_CHAIN_INVALID")
        if solana and r.get("mode", "paired") != "reference":
            raise ValueError("SOLANA_REFERENCE_ONLY")
        if any(not (mint_valid(r[k]) if solana else isinstance(r[k], str) and ADDRESS.fullmatch(r[k])) for k in ("stable", "asset")):
            raise ValueError("DEX_VIRTUAL_TOKEN_INVALID")
        if token_key(r, r["stable"]) == token_key(r, r["asset"]):
            raise ValueError("DEX_VIRTUAL_TOKEN_INVALID")
        if any(type(r[k]) is not int or not 0 <= r[k] <= 36 for k in ("stable_decimals", "asset_decimals", "native_decimals")):
            raise ValueError("DEX_VIRTUAL_DECIMALS_INVALID")
        if any(not isinstance(r[k], str) or not r[k].strip() for k in ("base", "cex_venue", "cex_symbol", "native_symbol")):
            raise ValueError("DEX_VIRTUAL_IDENTITY_INVALID")
        if r.get("mode", "paired") not in ("paired", "reference"):
            raise ValueError("DEX_VIRTUAL_MODE_INVALID")
        amount = number(r.get("notional_usd", 5), "DEX_VIRTUAL_NOTIONAL")
        if amount > 30:
            raise ValueError("DEX_VIRTUAL_MICRO_LIMIT")
        cap = number(r.get("max_notional_usd", min(30, float(amount) * (1.1 if r.get("mode", "paired") == "paired" else 1))), "DEX_VIRTUAL_CEX_CAP")
        if cap > 30:
            raise ValueError("DEX_VIRTUAL_MICRO_LIMIT")
        number(r.get("slippage_bps", 20), "DEX_VIRTUAL_SLIPPAGE", False)
        if float(r.get("slippage_bps", 20)) > 100:
            raise ValueError("DEX_VIRTUAL_SLIPPAGE_INVALID")
        if float(r.get("slippage_bps", 20)) != int(float(r.get("slippage_bps", 20))):
            raise ValueError("DEX_VIRTUAL_SLIPPAGE_INVALID")
        if r.get("stable_symbol", "USDT") not in ("USDT", "USDC"):
            raise ValueError("DEX_VIRTUAL_STABLE_INVALID")
        identity = route_key(r)
        if identity in identities:
            raise ValueError("DEX_VIRTUAL_DUPLICATE_ROUTE")
        identities.add(identity)
    return routes


def route_key(r):
    return (r["chain_id"], token_key(r, r["stable"]), token_key(r, r["asset"]),
            r["cex_venue"], r["cex_symbol"], r.get("mode", "paired"))


class Source(HistorySource):
    def __init__(self, provider, routes, public, funding_service, private=None, clock=time.time):
        self.provider, self.routes = provider, routes_checked(routes)
        self.public, self.private = public, private or {}
        self.fs, self.clock = funding_service, clock
        self.cycle = SimpleNamespace(public=public)
        self.reader = Reader(public, clock=clock)
        self.signals = None
        self.confirmations = {}
        self.focus = enabled("DEX_REFERENCE_FOCUS")
        self.signals_only = enabled("DEX_CHANNEL_SIGNALS_ONLY")
        self.entry_venues = None
        self.blocked_until = lambda r: 0
        self.consumed_signal = lambda r: 0

    def venue_allowed(self, venue):
        return venue in self.entry_venues if self.entry_venues is not None else entry_venue_allowed(venue)

    @staticmethod
    def cex_cap(r):
        cash = float(r.get("notional_usd", 5))
        return float(r.get("max_notional_usd", min(30, cash * (1.1 if r.get("mode", "paired") == "paired" else 1))))

    def reserve_for_route(self, r):
        cash = float(r.get("notional_usd", 5)) if r.get("mode", "paired") == "paired" else 0
        return max(12, cash + self.cex_cap(r) + 2)

    async def price(self, r, sell_asset, amount):
        if hasattr(self.provider, "price_route"):
            q = await self.provider.price_route(r, sell_asset, amount)
        else:
            q = await self.provider.price(r["chain_id"], r["asset"] if sell_asset else r["stable"],
                                      r["stable"] if sell_asset else r["asset"], str(amount))
        if not q.get("ok"):
            raise ValueError(q.get("reason", "DEX_VIRTUAL_PRICE_UNAVAILABLE"))
        if (q.get("chain_id"), token_key(r, q.get("sell_token", "")), token_key(r, q.get("buy_token", "")), str(q.get("sell_amount_raw"))) != (
            r["chain_id"], token_key(r, r["asset"] if sell_asset else r["stable"]),
            token_key(r, r["stable"] if sell_asset else r["asset"]), str(amount)):
            raise ValueError("DEX_VIRTUAL_PRICE_SCOPE_MISMATCH")
        if not 0 <= self.clock() - float(q["ts"]) <= 15:
            raise ValueError("DEX_VIRTUAL_PRICE_STALE")
        buy = int(number(q["buy_amount_raw"], "DEX_VIRTUAL_BUY"))
        haircut = Decimal(1) - Decimal(str(r.get("slippage_bps", 20))) / 10000
        minimum = int(Decimal(buy) * haircut)
        if q.get("min_buy_amount_raw") is not None:
            minimum = min(minimum, int(number(q["min_buy_amount_raw"], "DEX_VIRTUAL_MIN_BUY")))
        if minimum <= 0:
            raise ValueError("DEX_VIRTUAL_AMOUNT_TOO_SMALL")
        q = dict(q, virtual_min_buy_raw=str(minimum), live_allowed=False,
                 mode="DEX_VIRTUAL_INDICATIVE_MODEL", execution_verified=False)
        if r.get("mode", "paired") == "paired":
            metadata = q.get("token_metadata") or {}
            for side in ("sellToken", "buyToken"):
                for field in ("buyTaxBps", "sellTaxBps", "transferTaxBps"):
                    if number((metadata.get(side) or {}).get(field), "DEX_VIRTUAL_TAX", False) != 0:
                        raise ValueError("DEX_VIRTUAL_TOKEN_TAX_UNSUPPORTED")
        return q

    async def fee(self, r):
        c = self.private.get(r["cex_venue"])
        if c is not None and c.has.get("fetchTradingFee") is True:
            f = await asyncio.wait_for(c.fetch_trading_fee(r["cex_symbol"]), 8)
            if f.get("symbol") != r["cex_symbol"]:
                raise ValueError("DEX_VIRTUAL_FEE_SCOPE_MISMATCH")
            return float(rate(f.get("taker"))), "ACCOUNT_READ_ONLY"
        m = market(self.public[r["cex_venue"]], r["cex_symbol"])
        return float(rate(m.get("taker"))), "PUBLIC_RATE_ASSUMPTION"

    async def gas(self, r, q):
        if r.get("mode", "paired") == "reference":
            return 0, 0, {}
        network = number(q.get("network_fee_raw"), "DEX_VIRTUAL_GAS_UNKNOWN")
        c = self.public[r["cex_venue"]]
        start = self.clock()
        raw = await asyncio.wait_for(c.fetch_order_book(r["native_symbol"], limit=20), 8)
        book = normalize(raw, r["native_symbol"], start, self.clock(), 1.5)
        price = float(number(book["asks"][0][0], "DEX_VIRTUAL_NATIVE_PRICE"))
        usd = float(network / Decimal(10 ** r["native_decimals"])) * price
        if not math.isfinite(usd) or usd > 0.25:
            raise ValueError("DEX_VIRTUAL_GAS_BUDGET_LIMIT")
        return usd, price, book

    def route(self, p):
        matches = [r for r in self.routes if list(route_key(r)) == p["virtual_route_key"]]
        if len(matches) != 1:
            raise ValueError("DEX_VIRTUAL_ROUTE_CHANGED")
        r = matches[0]
        if [r[k] for k in ("base", "stable_decimals", "asset_decimals", "native_symbol", "native_decimals")] != p["virtual_identity"]:
            raise ValueError("DEX_VIRTUAL_IDENTITY_CHANGED")
        return r

    async def entry(self, r):
        diagnostics = {}
        try:
            if self.blocked_until(r) > self.clock():
                raise ValueError("DEX_SIGNAL_LOSS_COOLDOWN")
            if not self.venue_allowed(r["cex_venue"]):
                raise ValueError("DEX_VIRTUAL_ENTRY_VENUE_DISABLED")
            signal = None
            if self.focus:
                if r.get("mode") != "reference":
                    raise ValueError("DEX_REFERENCE_FOCUS_REFERENCE_ONLY")
                if self.signals is not None:
                    _, reason = self.signals.get(r)
                    if reason == "DEX_SIGNAL_LOSS_COOLDOWN":
                        raise ValueError(reason)
            if self.signals_only:
                if self.signals is None:
                    raise ValueError("DEX_CHANNEL_SIGNAL_REQUIRED")
                signal, reason = self.signals.get(r)
                if reason:
                    raise ValueError(reason)
                if signal["timestamp"] <= self.consumed_signal(r):
                    raise ValueError("DEX_CHANNEL_SIGNAL_ALREADY_USED")
            if r["cex_venue"] not in self.public:
                raise ValueError("DEX_VIRTUAL_CEX_UNAVAILABLE")
            c = self.public[r["cex_venue"]]
            m = market(c, r["cex_symbol"])
            if m["base"] != r["base"]:
                raise ValueError("DEX_VIRTUAL_CEX_BASE_MISMATCH")
            cash = float(number(r.get("notional_usd", 5), "DEX_VIRTUAL_NOTIONAL"))
            amount = int(Decimal(str(cash)) * 10 ** r["stable_decimals"])
            q = await self.price(r, False, amount)
            raw_qty = int(q["virtual_min_buy_raw"])
            base = float(Decimal(raw_qty) / Decimal(10 ** r["asset_decimals"]))
            hedge_base = base
            if r.get("mode", "paired") == "reference":
                sizing = await asyncio.wait_for(c.fetch_order_book(r["cex_symbol"], limit=20), 8)
                cap_price = float(number(sizing["asks"][0][0], "DEX_VIRTUAL_SIZING_PRICE"))
                hedge_base = min(base, self.cex_cap(r) / cap_price)
            size = number(m["contractSize"], "CONTRACT_SIZE")
            requested = Decimal(str(hedge_base)) / size
            step = quantum(c, m)
            diagnostics = dict(symbol=r["cex_symbol"], venue=r["cex_venue"],
                               requested_contracts=float(requested), notional_usd=cash,
                               amount_step=float(step / size) if step is not None else None,
                               minimum_contracts=(m.get("limits", {}).get("amount") or {}).get("min"),
                               minimum_cost=(m.get("limits", {}).get("cost") or {}).get("min"))
            if step is not None and Decimal(str(hedge_base)) < step:
                raise ValueError("DEX_VIRTUAL_AMOUNT_BELOW_NATIVE_STEP")
            contracts = float(format_value(c, r["cex_symbol"], requested, "amount"))
            qty = contracts * float(m["contractSize"])
            if qty > base:
                raise ValueError("DEX_VIRTUAL_HEDGE_ROUNDING_UP")
            reverse = await self.price(r, True, raw_qty)
            dex_bid = float(Decimal(reverse["virtual_min_buy_raw"]) / Decimal(10 ** r["stable_decimals"])) / base
            dex_ask = cash / base
            fee, fee_source = await self.fee(r)
            gas, gas_price, gas_book = await self.gas(r, q)
            sell = await self.reader.quote(r["cex_venue"], SubmitRequest(r["cex_symbol"], "sell", contracts, "market"), float(m["contractSize"]))
            side, native = "sell", sell
            if r.get("mode", "paired") == "reference":
                buy = await self.reader.quote(r["cex_venue"], SubmitRequest(r["cex_symbol"], "buy", contracts, "market"), float(m["contractSize"]))
                if dex_bid - buy.reference_price > sell.reference_price - dex_ask:
                    side, native = "buy", buy
            notional = qty * native.reference_price
            if notional > self.cex_cap(r) or (r.get("mode", "paired") == "paired" and abs(base - qty) * native.reference_price > 0.05):
                raise ValueError("DEX_VIRTUAL_NOTIONAL_OR_RESIDUAL_LIMIT")
            if self.fs is None:
                raise ValueError("DEX_FUNDING_CALENDAR_UNKNOWN")
            snapshot = await self.fs.get(r["cex_venue"], r["cex_symbol"])
            if snapshot.next_ts is None or snapshot.interval_hours is None:
                raise ValueError("DEX_FUNDING_CALENDAR_UNKNOWN")
            p = dict(symbol=r["cex_symbol"], cex_symbol=r["cex_symbol"], cex_venue=r["cex_venue"],
                     chain_id=r["chain_id"], base_qty=qty, cex_contracts=contracts,
                     contract_size=float(m["contractSize"]), cex_side=side, forward=True,
                     asset_amount_raw=str(raw_qty), asset_decimals=r["asset_decimals"], stable_decimals=r["stable_decimals"],
                     entry_cash=cash, entry_price=native.reference_price, entry_fee_pct=fee * 100,
                     entry_fees=notional * fee + gas, entry_gas=gas, entry_gas_price=gas_price,
                     entry_gas_book=gas_book, entry_dex=q, entry_cex=native.market_evidence,
                     native_decimals=r["native_decimals"], safety=max(cash, notional) * .001,
                     funding_next=float(snapshot.next_ts) / 1000, funding_period=float(snapshot.interval_hours) * 3600,
                     opened_at=self.clock(), reserve=self.reserve_for_route(r), model="DEX_VIRTUAL_INDICATIVE_MODEL",
                     virtual_mode=r.get("mode", "paired"), virtual_route_key=list(route_key(r)),
                     virtual_identity=[r[k] for k in ("base", "stable_decimals", "asset_decimals", "native_symbol", "native_decimals")],
                     virtual_asset_qty=base if r.get("mode", "paired") == "paired" else 0,
                     fee_source=fee_source, execution_verified=False, live_allowed=False,
                     dex_reference_bid=dex_bid, dex_reference_ask=dex_ask,
                     assumptions=["indicative DEX fill with slippage haircut", r.get("stable_symbol", "USDT") + " and USDT valued at 1 USD", "CEX visible book fill", "funding entry-reference notional"])
            calendar(p, p["opened_at"])
            x = await self.exit(p)
            if not x.get("ok"):
                return x
            # Reverse preflight performs IO; refresh entry book afterwards.
            native = await self.reader.quote(r["cex_venue"], SubmitRequest(r["cex_symbol"], side, contracts, "market"), p["contract_size"])
            notional = qty * native.reference_price
            if notional > self.cex_cap(r):
                raise ValueError("DEX_VIRTUAL_MICRO_LIMIT")
            p.update(entry_price=native.reference_price, entry_cex=native.market_evidence,
                     entry_fees=notional * fee + gas)
            edge = (qty * (native.reference_price - dex_ask) if side == "sell" else qty * (dex_bid - native.reference_price))
            if p["virtual_mode"] == "paired":
                edge = notional - cash
            p["entry_edge"] = edge - p["entry_fees"] - notional * x["fee_rate"] - x["gas"] - p["safety"]
            immediate_gross = (x["cash"] - cash if p["virtual_mode"] == "paired" else 0) + (1 if side == "buy" else -1) * qty * (x["price"] - p["entry_price"])
            immediate_net = immediate_gross - p["entry_fees"] - qty * x["price"] * x["fee_rate"] - x["gas"] - p["safety"]
            if immediate_net <= -max(cash, notional) * 0.02:
                raise ValueError("DEX_VIRTUAL_REVERSE_PREFLIGHT_NET_STOP")
            if self.focus and p["virtual_mode"] == "reference":
                if signal and signal["side"] != side:
                    raise ValueError("DEX_SIGNAL_DIRECTION_CHANGED")
                failure = quality(notional, p["entry_edge"], immediate_net, native.market_evidence)
                diagnostics.update(entry_edge=p["entry_edge"], immediate_net=immediate_net)
                key = route_key(r)
                if failure:
                    self.confirmations.pop(key, None)
                    raise ValueError(failure)
                previous = self.confirmations.get(key)
                identity = (side, signal["timestamp"] if signal else None)
                self.confirmations[key] = (self.clock(), identity)
                if not previous or previous[1] != identity or not 2 <= self.clock() - previous[0] <= 60:
                    raise ValueError("DEX_REFERENCE_CONFIRMATION_REQUIRED")
                if signal:
                    # Quote requests can outlive the signal TTL; validate again.
                    current, reason = self.signals.get(r)
                    if reason or current != signal:
                        raise ValueError(reason or "DEX_SIGNAL_CHANGED")
                    p["channel_signal"] = signal
            if self.clock() - min(q["ts"], reverse["ts"]) > 15 or self.clock() - native.market_evidence["book_ts"] > 1.5:
                raise ValueError("DEX_VIRTUAL_ENTRY_STALE")
            if self.clock() - x["cex"]["book_ts"] > 1.5 or (gas_book and self.clock() - gas_book["timestamp"] / 1000 > 1.5):
                raise ValueError("DEX_VIRTUAL_ENTRY_STALE")
            p["opened_at"] = self.clock()
            calendar(p, p["opened_at"])
            return dict(ok=True, position=p)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return dict(ok=False, reason=str(exc) if isinstance(exc, ValueError) else "DEX_VIRTUAL_ENTRY_UNAVAILABLE", diagnostics=diagnostics)

    async def exit(self, p):
        try:
            r = self.route(p)
            if float(market(self.public[r["cex_venue"]], r["cex_symbol"])["contractSize"]) != p["contract_size"]:
                raise ValueError("DEX_VIRTUAL_CONTRACT_CHANGED")
            # Refresh reverse executable-size indicative price even for reference mode.
            if p["virtual_mode"] == "reference":
                try:
                    q = await self.price(r, True, p["asset_amount_raw"])
                except Exception as exc:
                    # The position is CEX-only. Losing the DEX anchor must not
                    # prevent pricing a CEX risk/time/profit exit.
                    q = dict(ok=False, reason=str(exc) if isinstance(exc, ValueError) else "DEX_REFERENCE_UNAVAILABLE",
                             mode="DEX_VIRTUAL_INDICATIVE_MODEL", execution_verified=False)
            else:
                q = await self.price(r, True, p["asset_amount_raw"])
            fee, _ = await self.fee(r)
            gas, gas_price, gas_book = await self.gas(r, q)
            native = await self.reader.quote(r["cex_venue"], SubmitRequest(r["cex_symbol"], "buy" if p["cex_side"] == "sell" else "sell", p["cex_contracts"], "market", reduce_only=True), p["contract_size"])
            if q.get("ok") and not 0 <= self.clock() - q["ts"] <= 15:
                raise ValueError("DEX_VIRTUAL_EXIT_STALE")
            return dict(ok=True, ts=self.clock(), dex=q, cex=native.market_evidence,
                        price=native.reference_price, fee_rate=fee, gas=gas,
                        gas_price=gas_price, gas_book=gas_book,
                        cash=float(Decimal(q["virtual_min_buy_raw"]) / Decimal(10 ** p["stable_decimals"])) if p["virtual_mode"] == "paired" else p["entry_cash"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return dict(ok=False, reason=str(exc) if isinstance(exc, ValueError) else "DEX_VIRTUAL_EXIT_UNAVAILABLE")

    def mark(self, p, x, h):
        result = paired_mark(p, x, h)
        result.update(mode="DEX_VIRTUAL_INDICATIVE_MODEL", virtual_mode=p["virtual_mode"],
                      execution_verified=False, live_allowed=False, fee_source=p["fee_source"])
        return result
