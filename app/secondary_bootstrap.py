import os, json
from pathlib import Path
from .dex_firm_simulation import Provider as FirmProvider, Cycle as FirmCycle
from .zerox_research import Provider as DexProvider, Cycle as DexCycle
from .public_client_factory import build, close
from .spot_future_service import Service as SpotFutureService
from .spot_future_cycle_service import CycleService as SpotFutureCycle
from .spot_future_paper_engine import Engine as SpotFuturePaper
from .strategy_paper_coordinator import Coordinator
from .spot_spot_service import Service as SpotSpotService
from .spot_spot_paper import Engine as SpotSpotPaper
from .strategy_universe import common_spot_symbols
from .secondary_strategy_runtime import SecondaryRuntime
from .funding_arb_service import Service as FundingService
from .funding_cycle_service import CycleService as FundingCycle
from .funding_paper import Engine as FundingPaper
from .funding_paper_source import Source as FundingPaperSource


class Bundle:
    def __init__(self, clients, runtime, sf_paper):
        self.clients = clients
        self.runtime = runtime
        self.sf_paper = sf_paper
        self.dex_provider = None
        self.dex_sim_provider = None
        self.ss_paper = None
        self.funding_paper = None

    async def close(self):
        await self.runtime.stop()
        await close(self.clients)
        if self.dex_provider:
            await self.dex_provider.close()
        if self.dex_sim_provider:
            await self.dex_sim_provider.close()


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
    private_clients=None,
):
    clients = await build(ids)
    bundle = None
    try:
        sf = SpotFutureService(clients, notional, min_edge)
        await sf.start()
        paper = SpotFuturePaper()
        coord = Coordinator(paper)
        cycle = SpotFutureCycle(sf, coord, min_edge, db_path)
        await cycle.restore()
        sr = SecondaryRuntime(runtime, record, db_path, interval)
        sr.add("spot_futures", cycle)
        inventory = json.loads(os.getenv("PAPER_SPOT_INVENTORY_JSON", "{}"))
        if not isinstance(inventory, dict):
            raise ValueError("PAPER_SPOT_INVENTORY_INVALID")
        ss_paper = SpotSpotPaper(
            db_path,
            inventory,
            capital=float(os.getenv("PAPER_CAPITAL_USD", "50")),
            entry_edge=min_edge,
        )
        await ss_paper.init()
        sr.add(
            "spot_spot",
            SpotSpotService(
                clients, common_spot_symbols(clients), notional, batch=5, paper=ss_paper
            ),
        )
        fp = None
        if funding_service:
            fp = FundingPaper(
                db_path,
                FundingPaperSource(funding_service.clients, funding_service, notional),
                capital=float(os.getenv("PAPER_CAPITAL_USD", "50")),
                max_seconds=max(
                    60, int(os.getenv("FUNDING_PAPER_HOLD_SECONDS", "28800"))
                ),
                min_carry=float(os.getenv("FUNDING_PAPER_MIN_CARRY_PCT", "0.03")),
            )
            await fp.init()
            sr.add(
                "funding_arb",
                FundingCycle(
                    FundingService(funding_service, list(funding_service.clients)),
                    future_symbols,
                    paper=fp,
                ),
            )
        bundle = Bundle(clients, sr, paper)
        bundle.ss_paper = ss_paper
        bundle.funding_paper = fp
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
        simulation_routes = json.loads(os.getenv("DEX_SIMULATION_ROUTES_JSON", "[]"))
        if simulation_routes:
            required = {
                "chain_id",
                "sell_token",
                "buy_token",
                "sell_amount_raw",
                "taker",
            }
            if not isinstance(simulation_routes, list) or not all(
                isinstance(x, dict) and required <= x.keys() for x in simulation_routes
            ):
                raise ValueError("DEX_SIMULATION_ROUTES_INVALID")
            registry = json.loads(
                Path(
                    os.getenv("DEX_TOKEN_REGISTRY_PATH", "dex_token_registry.json")
                ).read_text()
            )
            bundle.dex_sim_provider = FirmProvider(
                os.getenv("ZEROX_API_KEY"), os.getenv("DEX_SIMULATION_RPC_URL")
            )
            sr.add(
                "cex_dex",
                FirmCycle(
                    bundle.dex_sim_provider,
                    simulation_routes,
                    registry,
                    funding_service.clients if funding_service else {},
                    private_clients,
                ),
            )
            runtime.enabled["cex_dex"] = True
        return bundle
    except BaseException:
        if bundle and bundle.dex_provider:
            await bundle.dex_provider.close()
        if bundle and bundle.dex_sim_provider:
            await bundle.dex_sim_provider.close()
        await close(clients)
        raise
