import os, json
import aiosqlite
import aiohttp
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
from .funding_rate_history import Store as FundingRateHistory
from .cex_dex_paper_source import Source as DexPaperSource
from .cex_dex_paper import Engine as DexPaper, Cycle as DexPaperCycle
from .dex_execution_stress import History as DexHistory
from .dex_wallet import Journal as WalletJournal, RPC as WalletRPC
from .dex_wallet_observer import Observer as WalletObserver
from .dex_live_observer import build as dex_bridge_observer
from .dex_live_bootstrap import build as build_dex_live
from .secondary_book_history import attach as attach_book_history


class Bundle:
    def __init__(self, clients, runtime, sf_paper):
        self.clients = clients
        self.runtime = runtime
        self.sf_paper = sf_paper
        self.dex_provider = None
        self.dex_sim_provider = None
        self.ss_paper = None
        self.funding_paper = None
        self.dex_paper = None
        self.wallet_session = None
        self.dex_live = None
        self.book_recorder = None

    async def close(self):
        await self.runtime.stop()
        await close(self.clients)
        if self.book_recorder:
            await self.book_recorder.close()
        if self.dex_provider:
            await self.dex_provider.close()
        if self.dex_sim_provider:
            await self.dex_sim_provider.close()
        if self.wallet_session:
            await self.wallet_session.close()
        if self.dex_live:
            await self.dex_live.provider.close()


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
    dex_options=None,
    book_history=None,
    book_record_interval=1,
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
            funding_history = FundingRateHistory(db_path)
            await funding_history.init()
            fp = FundingPaper(
                db_path,
                FundingPaperSource(
                    funding_service.clients,
                    funding_service,
                    notional,
                    history_store=funding_history,
                    book_history=book_history,
                ),
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
        wallet = WalletJournal(db_path)
        await wallet.init()
        rpc_a, rpc_b = os.getenv("DEX_WALLET_RPC_PRIMARY"), os.getenv(
            "DEX_WALLET_RPC_SECONDARY"
        )
        primary = secondary_rpc = None
        if rpc_a or rpc_b:
            if not rpc_a or not rpc_b or rpc_a == rpc_b:
                raise ValueError("DEX_WALLET_DUAL_RPC_REQUIRED")
            bundle.wallet_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            )
            primary, secondary_rpc = WalletRPC(rpc_a, bundle.wallet_session), WalletRPC(
                rpc_b, bundle.wallet_session
            )
            sr.add(
                "wallet_receipts",
                WalletObserver(
                    wallet,
                    primary,
                    secondary_rpc,
                ),
            )
            sr.add(
                "dex_live_observation",
                await dex_bridge_observer(
                    db_path, wallet, primary, secondary_rpc, private_clients or {}
                ),
            )
        else:
            async with aiosqlite.connect(db_path) as d:
                async with d.execute(
                    "SELECT COUNT(*) FROM wallet_tx_intents WHERE phase NOT IN ('FINALIZED_SUCCESS','FINALIZED_REVERT')"
                ) as c:
                    if (await c.fetchone())[0]:
                        raise ValueError("DEX_PENDING_WALLET_REQUIRES_DUAL_RPC")
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
        if not simulation_routes:
            async with aiosqlite.connect(db_path) as d:
                async with d.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='cex_dex_paper'"
                ) as c:
                    has_dex_history = await c.fetchone()
                if has_dex_history:
                    async with d.execute(
                        "SELECT COUNT(*) FROM cex_dex_paper WHERE status!='CLOSED'"
                    ) as c:
                        if (await c.fetchone())[0]:
                            raise ValueError(
                                "DEX_OPEN_PAPER_REQUIRES_CONFIGURED_ROUTES"
                            )
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
            firm_cycle = FirmCycle(
                bundle.dex_sim_provider,
                simulation_routes,
                registry,
                funding_service.clients if funding_service else {},
                private_clients,
            )
            dex_history = DexHistory(db_path)
            await dex_history.init()
            bundle.dex_paper = DexPaper(
                db_path,
                DexPaperSource(firm_cycle, funding_service, history=dex_history),
                capital=float(os.getenv("PAPER_CAPITAL_USD", "50")),
                max_seconds=float(os.getenv("DEX_PAPER_HOLD_SECONDS", "900")),
            )
            await bundle.dex_paper.init()
            sr.add("cex_dex", DexPaperCycle(bundle.dex_paper, simulation_routes))
            runtime.enabled["cex_dex"] = True
        bundle.dex_live = await build_dex_live(
            db_path,
            wallet,
            primary,
            secondary_rpc,
            clients,
            private_clients or {},
            funding_service,
            dex_options,
        )
        if bundle.dex_live:
            sr.add("dex_live_candidates", bundle.dex_live.source)
            runtime.enabled["cex_dex"] = True
        if book_history is not None:
            bundle.book_recorder = attach_book_history(
                book_history, clients, book_record_interval
            )
        return bundle
    except BaseException:
        if bundle and bundle.dex_provider:
            await bundle.dex_provider.close()
        if bundle and bundle.dex_sim_provider:
            await bundle.dex_sim_provider.close()
        if bundle and bundle.wallet_session:
            await bundle.wallet_session.close()
        if bundle and bundle.dex_live:
            await bundle.dex_live.provider.close()
        if bundle and bundle.book_recorder:
            await bundle.book_recorder.close()
        await close(clients)
        raise
