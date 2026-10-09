from .spot_future_source import SpotFutureSource
from .spot_future_candidate import check as candidate
from .strategy_observation import row
from .spot_future_native_plan import prepare


class Service:
    def __init__(self, clients, notional, min_edge, fee_pct=0.2, safety_pct=0.1):
        self.source = SpotFutureSource(clients, notional, fee_pct, safety_pct)
        self.min_edge = min_edge

    async def start(self):
        await self.source.load()

    async def cycle(self):
        rows = await self.source.scan()
        accepted = []
        for x in rows:
            # Discovery keeps unknown funding visible, but Paper promotion requires evidence later.
            x["fee_verified"] = False
            x["funding_known"] = False
            x["evidence_mode"] = "RESEARCH_ESTIMATE"
            if x.get("direction") == "LONG_SPOT_SHORT_FUTURE":
                client = self.source.clients[x["exchange"]]
                x["native_plan"] = prepare(
                    client,
                    client,
                    x["spot_symbol"],
                    x["future_symbol"],
                    x["base_qty"],
                    x["prices"]["spot_buy"],
                    x["prices"]["future_sell"],
                    # Public estimate reserves base fees, never certifies account tier.
                    float(x["fee_pct"]) / 100,
                ).row()
            else:
                x["native_plan"] = {
                    "valid": False,
                    "reason": "SPOT_BORROWING_UNVERIFIED",
                    "release_authorized": False,
                }
            accepted.append(x)
        return accepted, [row(x) for x in rows]
