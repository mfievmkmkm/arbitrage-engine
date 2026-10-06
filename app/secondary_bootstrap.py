from .public_client_factory import build,close
from .spot_future_service import Service as SpotFutureService
from .spot_future_cycle_service import CycleService as SpotFutureCycle
from .spot_future_paper_engine import Engine as SpotFuturePaper
from .strategy_paper_coordinator import Coordinator
from .spot_spot_service import Service as SpotSpotService
from .strategy_universe import common_spot_symbols
from .secondary_strategy_runtime import SecondaryRuntime
class Bundle:
 def __init__(self,clients,runtime,sf_paper):self.clients=clients;self.runtime=runtime;self.sf_paper=sf_paper
 async def close(self):await self.runtime.stop();await close(self.clients)
async def build_bundle(ids,notional,min_edge,interval,runtime,record,db_path):
 clients=await build(ids);sf=SpotFutureService(clients,notional,min_edge);await sf.start();paper=SpotFuturePaper();coord=Coordinator(paper);sr=SecondaryRuntime(runtime,record,db_path,interval);sr.add("spot_futures",SpotFutureCycle(sf,coord,min_edge));universe=common_spot_symbols(clients);sr.add("spot_spot",SpotSpotService(clients,universe,notional,batch=5));return Bundle(clients,sr,paper)
