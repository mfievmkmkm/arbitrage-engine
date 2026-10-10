from abc import ABC,abstractmethod
from dataclasses import dataclass
@dataclass(frozen=True)
class PrivatePosition:
 venue:str;symbol:str;side:str;qty:float;entry_price:float|None=None;contracts:float|None=None;contract_size:float=1.0
@dataclass(frozen=True)
class PrivateOrder:
 venue:str;symbol:str;order_id:str;side:str;qty:float;filled:float;status:str;contracts:float|None=None;filled_contracts:float|None=None;contract_size:float=1.0
class PrivateAdapter(ABC):
 @abstractmethod
 async def positions(self):...
 @abstractmethod
 async def open_orders(self):...
 @abstractmethod
 async def cancel(self,order_id,symbol):...
 @abstractmethod
 async def flatten(self,symbol):...
class DisabledAdapter(PrivateAdapter):
 async def positions(self):return []
 async def open_orders(self):return []
 async def cancel(self,order_id,symbol):raise RuntimeError("LIVE_DISABLED")
 async def flatten(self,symbol):raise RuntimeError("LIVE_DISABLED")
