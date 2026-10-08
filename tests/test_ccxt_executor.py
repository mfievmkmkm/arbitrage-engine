import asyncio
from app.ccxt_executor import CCXTExecutor
from app.exchange_executor import SubmitRequest


class C:
    precisionMode = 4

    def market(self, symbol):
        return dict(
            symbol=symbol,
            contract=True,
            linear=True,
            base="X",
            quote="USDT",
            settle="USDT",
            contractSize=1,
            precision=dict(amount=1, price=0.01),
            limits={"amount": {"min": 1}},
        )

    def amount_to_precision(self, symbol, amount):
        return str(int(float(amount)))

    def price_to_precision(self, symbol, price):
        return str(round(float(price), 2))

    async def create_order(self, *a):
        return {
            "id": "1",
            "status": "closed",
            "filled": 2,
            "average": 101,
            "fee": {"cost": 0.02, "currency": "USDT"},
        }

    async def cancel_order(self, *a):
        return {"id": "1", "status": "canceled", "filled": 1}

    async def fetch_order(self, *a):
        return {"id": "1", "status": "open", "filled": 1}


def test_normalize():
    x = asyncio.run(
        CCXTExecutor("x", C()).submit(SubmitRequest("X", "buy", 2, price=101))
    )
    assert x.filled == 2 and x.avg_price == 101 and x.fee == 0.02
