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

    async def create_order(self, *args):
        self.args = args
        return {
            "id": "1",
            "status": "closed",
            "filled": 1,
            "amount": 1,
            "average": 100,
            "fee": {"cost": 0, "currency": "USDT"},
        }

    async def fetch_orders(self, symbol):
        return [
            {
                "id": "1",
                "clientOrderId": "cid",
                "status": "closed",
                "filled": 1,
                "amount": 1,
                "average": 100,
                "fee": {"cost": 0, "currency": "USDT"},
            }
        ]


def test_ccxt_client_order_id_sent_and_lookup_supported():
    async def go():
        c = C()
        e = CCXTExecutor("x", c)
        await e.submit(
            SubmitRequest("X", "buy", 1, "market", None, False, False, "cid", 100)
        )
        assert c.args[-1]["clientOrderId"] == "cid"
        r = await e.order_by_client_id("cid", "X")
        assert r.order_id == "1" and r.filled == 1

    asyncio.run(go())
