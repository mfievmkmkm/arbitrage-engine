import asyncio
from app.account_balances import snapshot, render


class Client:
    def __init__(self, value): self.value = value
    async def fetch_balance(self):
        if isinstance(self.value, Exception): raise self.value
        return self.value


def test_real_balances_unknown_errors_and_shared_scopes():
    async def run():
        client = Client({'free': {'USDT': 12}, 'used': {'USDT': 3}, 'total': {'USDT': 15}})
        rows = await snapshot({'ok': client, 'bad': Client(ValueError('secret-signed-url'))},
                              {'ok': client, 'unknown': Client({})})
        text = render(rows)
        assert text.count('12 / 3 / 15') == 2
        assert 'secret-signed-url' not in text
        assert 'Недоступно · ValueError' in text
        assert 'USDT: — / — / —' in text
        assert 'не суммируются' in text
    asyncio.run(run())


def test_balance_timeout_and_non_finite():
    class Slow:
        async def fetch_balance(self): await asyncio.Event().wait()
    async def run():
        rows = await snapshot({'slow': Slow(), 'bad': Client({'USDT': {'free': float('nan')}})}, {}, timeout=.01)
        text = render(rows)
        assert 'TimeoutError' in text
        assert 'nan' not in text
    asyncio.run(run())
