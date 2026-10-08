from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SubmitRequest:
    symbol: str
    side: str
    qty: float
    order_type: str = "limit"
    price: float | None = None
    reduce_only: bool = False
    ioc: bool = False
    client_order_id: str | None = None
    reference_price: float | None = None


@dataclass(frozen=True)
class SubmitResult:
    order_id: str
    status: str
    filled: float = 0.0
    avg_price: float | None = None
    fee: float = 0.0


class ExchangeExecutor(ABC):
    @abstractmethod
    async def submit(self, request): ...
    @abstractmethod
    async def cancel(self, order_id, symbol): ...
    @abstractmethod
    async def order(self, order_id, symbol): ...
class DisabledExecutor(ExchangeExecutor):
    async def submit(self, request):
        raise RuntimeError("LIVE_EXECUTION_DISABLED")

    async def cancel(self, order_id, symbol):
        raise RuntimeError("LIVE_EXECUTION_DISABLED")

    async def order(self, order_id, symbol):
        raise RuntimeError("LIVE_EXECUTION_DISABLED")
