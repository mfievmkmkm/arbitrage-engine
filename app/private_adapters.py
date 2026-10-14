from dataclasses import dataclass
from abc import ABC,abstractmethod
@dataclass(frozen=True)
class PositionState:
    venue:str;symbol:str;side:str;qty:float;entry_price:float|None
@dataclass(frozen=True)
class OrderState:
    venue:str;order_id:str;symbol:str;side:str;qty:float;filled:float;status:str
class PrivateAdapter(ABC):
    @abstractmethod
    async def positions(self):...
    @abstractmethod
    async def open_orders(self):...
    @abstractmethod
    async def cancel_all(self,symbol=None):...
    @abstractmethod
    async def flatten(self,symbol):...
class ReadOnlyPrivateAdapter(PrivateAdapter):
    async def positions(self):return []
    async def open_orders(self):return []
    async def cancel_all(self,symbol=None):raise RuntimeError("LIVE_DISABLED")
    async def flatten(self,symbol):raise RuntimeError("LIVE_DISABLED")
