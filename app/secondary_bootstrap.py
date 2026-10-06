import os, json
from .zerox_research import Provider as DexProvider, Cycle as DexCycle
from .public_client_factory import build, close
from .spot_future_service import Service as SpotFutureService
from .spot_future_cycle_service import CycleService as SpotFutureCycle
from .spot_future_paper_engine import Engine as SpotFuturePaper
from .strategy_paper_coordinator import Coordinator
from .spot_spot_service import Service as SpotSpotService
from .strategy_universe import common_spot_symbols
from .secondary_strategy_runtime import SecondaryRuntime
from .funding_arb_service import Service as FundingService
from .funding_cycle_service import CycleService as FundingCycle


class Bundle:
    def __init__(self, clients, runtime, sf_paper):
        self.clients = clients
        self.runtime = runtime
        self.sf_paper = sf_paper
        self.dex_provider = None

    async def close(self):
        await self.runtime.stop()
        await close(self.clients)
        if self.dex_provider:
            await self.dex_provider.close()


async def build_bundle(
    ids,
    notional,
    min_edge,
    interval,
    runtime,
    record,
    db_path,
    funding_service=None,
    future_symbols=(),
):
    clients = await build(ids)
    try:
        sf = SpotFutureService(clients, notional, min_edge)
        await sf.start()
        paper = SpotFuturePaper()
        coord = Coordinator(paper)
        cycle = SpotFutureCycle(sf, coord, min_edge, db_path)
        await cycle.restore()
        sr = SecondaryRuntime(runtime, record, db_path, interval)
        sr.add("spot_futures", cycle)
        sr.add(
            "spot_spot",
            SpotSpotService(clients, common_spot_symbols(clients), notional, batch=5),
        )
        if funding_service:
            sr.add(
                "funding_arb",
                FundingCycle(
                    FundingService(funding_service, list(funding_service.clients)),
                    future_symbols,
                ),
            )
        bundle = Bundle(clients, sr, paper)
        routes = json.loads(os.getenv("DEX_RESEARCH_ROUTES_JSON", "[]"))
        if routes and os.getenv("ZEROX_API_KEY"):
            required = {"chain_id", "sell_token", "buy_token", "sell_amount_raw"}
            if not isinstance(routes, list) or not all(
                isinstance(x, dict) and required <= x.keys() for x in routes
            ):
                raise ValueError("DEX_RESEARCH_ROUTES_INVALID")
            bundle.dex_provider = DexProvider(os.getenv("ZEROX_API_KEY"))
            sr.add("cex_dex", DexCycle(bundle.dex_provider, routes))
            runtime.enabled["cex_dex"] = True
        return bundle
    except BaseException:
        await close(clients)
        raise
