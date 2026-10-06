import asyncio
from app.ccxt_executor import CCXTExecutor
from app.exchange_executor import SubmitRequest


class C:
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
            SubmitRequest("X", "buy", 1, "market", None, False, False, "cid")
        )
        assert c.args[-1]["clientOrderId"] == "cid"
        r = await e.order_by_client_id("cid", "X")
        assert r.order_id == "1" and r.filled == 1

    asyncio.run(go())
