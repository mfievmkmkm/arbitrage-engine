import time, math, copy
from dataclasses import dataclass, asdict


@dataclass
class PaperPosition:
    id: int
    symbol: str
    buy: str
    sell: str
    notional: float
    entry_buy: float
    entry_sell: float
    entry_spread: float
    opened_at: float
    best_net_usd: float = 0.0
    current_net_usd: float = 0.0
    current_spread: float = 0.0
    status: str = "OPEN"
    base_qty: float | None = None
    entry_fees_usd: float | None = None
    safety_usd: float = 0
    last_mark: dict | None = None


class PaperEngine:
    def __init__(
        self,
        diary,
        capital=50,
        max_positions=2,
        target=0.70,
        trailing=0.20,
        max_seconds=1200,
    ):
        self.diary = diary
        self.capital = capital
        self.budget = lambda: self.capital
        self.max_positions = max_positions
        self.target = target
        self.trailing = trailing
        self.max_seconds = max_seconds
        self.positions = {}
        self.external_reserved = lambda: 0
        self.pending_capital = 0.0

    async def restore(self):
        for row in await self.diary.open_paper_positions():
            p = PaperPosition(**row)
            self.positions[p.id] = p

    @property
    def used_capital(self):
        return self.pending_capital + sum(
            (p.base_qty or p.notional / p.entry_buy) * (p.entry_buy + p.entry_sell)
            + (p.entry_fees_usd or 0)
            + p.safety_usd
            for p in self.positions.values()
        )

    def can_open(self, o):
        if "native_plan" in o and o["native_plan"].get("valid") is not True:
            return False
        try:
            values = (
                o["notional"],
                o["entry_buy"],
                o["entry_sell"],
                (
                    o["base_qty"]
                    if o.get("base_qty") is not None
                    else o["notional"] / o["entry_buy"]
                ),
            )
            if not all(math.isfinite(float(x)) and float(x) > 0 for x in values):
                return False
            if any(
                not math.isfinite(float(o[k])) or float(o[k]) < 0
                for k in ("fee_pct", "safety_pct")
                if k in o
            ):
                return False
            if any(
                not math.isfinite(float(o[k])) or float(o[k]) <= 0
                for k in ("exit_buy", "exit_sell")
                if k in o
            ):
                return False
            if any(
                not math.isfinite(float(o[k]))
                for k in ("exit_spread", "executable", "ts", "decision_at")
                if k in o
            ):
                return False
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            return False
        return (
            len(self.positions) < self.max_positions
            and self.used_capital
            + self.external_reserved()
            + (
                o["base_qty"]
                if o.get("base_qty") is not None
                else o["notional"] / o["entry_buy"]
            )
            * (o["entry_buy"] + o["entry_sell"])
            * (1 + o.get("fee_pct", 0) / 400)
            + o["notional"] * o.get("safety_pct", 0) / 100
            <= self.budget()
            and not any(p.symbol == o["symbol"] for p in self.positions.values())
        )

    async def open(self, o):
        if not self.can_open(o):
            return None
        p = PaperPosition(
            0,
            o["symbol"],
            o["buy"],
            o["sell"],
            o["notional"],
            o["entry_buy"],
            o["entry_sell"],
            o["executable"],
            o.get("decision_at", o.get("ts", time.time())),
            current_spread=o["executable"],
            base_qty=(
                o["base_qty"]
                if o.get("base_qty") is not None
                else o["notional"] / o["entry_buy"]
            ),
            safety_usd=o["notional"] * o.get("safety_pct", 0) / 100,
        )
        if "fee_pct" in o:
            p.entry_fees_usd = (
                p.base_qty * (p.entry_buy + p.entry_sell) * o["fee_pct"] / 400
            )
        reserved = (
            p.base_qty * (p.entry_buy + p.entry_sell)
            + (p.entry_fees_usd or 0)
            + p.safety_usd
        )
        if p.entry_fees_usd is not None and all(
            k in o for k in ("exit_buy", "exit_sell", "exit_spread")
        ):
            gross = p.base_qty * (
                (o["exit_buy"] - p.entry_buy) + (p.entry_sell - o["exit_sell"])
            )
            exit_fees = (
                p.base_qty * (o["exit_buy"] + o["exit_sell"]) * o["fee_pct"] / 400
            )
            p.current_net_usd = gross - p.entry_fees_usd - exit_fees - p.safety_usd
            p.current_spread = o["exit_spread"]
            p.best_net_usd = max(0, p.current_net_usd)
            p.last_mark = dict(
                ts=p.opened_at,
                base_qty=p.base_qty,
                gross=gross,
                entry_fees=p.entry_fees_usd,
                exit_fees=exit_fees,
                safety=p.safety_usd,
                funding=0,
                net=p.current_net_usd,
                exit_spread=p.current_spread,
                mode="PAPER_MODEL",
            )
        self.pending_capital += reserved
        try:
            p.id = await self.diary.create_paper_position(asdict(p))
            self.positions[p.id] = p
        finally:
            self.pending_capital -= reserved
        return p

    async def mark_and_exit(self, ops):
        lookup = {(o["symbol"], o["buy"], o["sell"]): o for o in ops}
        closed = []
        for p in list(self.positions.values()):
            o = lookup.get((p.symbol, p.buy, p.sell))
            if not o:
                continue
            if o.get("decision_at", o.get("ts", time.time())) < (p.last_mark or {}).get(
                "ts", p.opened_at
            ):
                continue
            if o.get("ts") is not None and (
                not math.isfinite(o["ts"]) or not 0 <= time.time() - o["ts"] <= 12
            ):
                continue
            if any(
                not math.isfinite(float(o[k])) or float(o[k]) <= 0
                for k in ("exit_buy", "exit_sell")
            ):
                continue
            if not math.isfinite(o["fee_pct"]) or o["fee_pct"] < 0:
                continue
            if not math.isfinite(float(o.get("settled_funding_pct", 0))):
                continue
            qty = p.base_qty or p.notional / p.entry_buy
            if o.get("base_qty") is not None and not math.isclose(
                o["base_qty"], qty, rel_tol=1e-8
            ):
                continue
            p = copy.deepcopy(p)
            p.base_qty = qty
            gross = (
                (o["exit_buy"] - p.entry_buy) + (p.entry_sell - o["exit_sell"])
            ) * qty
            if p.entry_fees_usd is None:
                p.entry_fees_usd = (
                    qty * (p.entry_buy + p.entry_sell) * o["fee_pct"] / 400
                )
            exit_fees = qty * (o["exit_buy"] + o["exit_sell"]) * o["fee_pct"] / 400
            fees = p.entry_fees_usd + exit_fees
            funding = p.notional * float(o.get("settled_funding_pct", 0)) / 100
            p.current_net_usd = gross - fees - p.safety_usd + funding
            p.last_mark = dict(
                ts=o.get("decision_at", o.get("ts", time.time())),
                quote_ts=o.get("ts"),
                base_qty=qty,
                gross=gross,
                entry_fees=p.entry_fees_usd,
                exit_fees=exit_fees,
                safety=p.safety_usd,
                funding=funding,
                exit_spread=o["exit_spread"],
                net=p.current_net_usd,
                mode="PAPER_MODEL",
            )
            p.best_net_usd = max(p.best_net_usd, p.current_net_usd)
            p.current_spread = o["exit_spread"]
            await self.diary.update_paper_position(asdict(p))
            self.positions[p.id].__dict__.update(p.__dict__)
            p = self.positions[p.id]
            conv = 1 - p.current_spread / p.entry_spread if p.entry_spread > 0 else 0
            reason = None
            if conv >= self.target and p.current_net_usd > 0:
                reason = "TARGET"
            elif p.best_net_usd > 0 and p.current_net_usd <= p.best_net_usd * (
                1 - self.trailing
            ):
                reason = "TRAILING"
            elif time.time() - p.opened_at >= self.max_seconds:
                reason = "TIME_STOP"
            if reason:
                closed.append(await self.close(p.id, reason))
        return closed

    async def close(self, pid, reason="MANUAL"):
        p = self.positions.get(pid)
        if not p:
            return None
        await self.diary.close_paper_position(dict(asdict(p), status="CLOSED"), reason)
        p.status = "CLOSED"
        self.positions.pop(pid, None)
        return p
