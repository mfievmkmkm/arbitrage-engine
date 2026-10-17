"""Read-only balances. Unknown is never zero; account scopes are never summed."""
import asyncio
import math
from html import escape


def amount(value):
    if value is None or isinstance(value, bool):
        return '—'
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return '—'
    return f'{value:,.8f}'.rstrip('0').rstrip('.') if math.isfinite(value) else '—'


async def snapshot(spot, futures, timeout=8):
    semaphore = asyncio.Semaphore(3)

    async def read(venue, scope, client):
        async with semaphore:
            try:
                balance = await asyncio.wait_for(client.fetch_balance(), timeout)
                if not isinstance(balance, dict):
                    raise ValueError('BALANCE_INVALID')
                assets = set(balance.get('free') or {}) | set(balance.get('used') or {}) | set(balance.get('total') or {})
                assets |= {k for k, v in balance.items() if k not in ('info', 'free', 'used', 'total') and isinstance(v, dict) and any(x in v for x in ('free', 'used', 'total'))}
                rows = []
                for asset in sorted(assets):
                    row = balance.get(asset) or {}
                    values = {key: row.get(key, (balance.get(key) or {}).get(asset)) for key in ('free', 'used', 'total')}
                    if asset == 'USDT' or any(amount(v) not in ('—', '0') for v in values.values()):
                        rows.append(dict(asset=asset, **values))
                if not any(r['asset'] == 'USDT' for r in rows):
                    rows.insert(0, dict(asset='USDT', free=None, used=None, total=None))
                return dict(venue=venue, scope=scope, assets=rows, error=None)
            except Exception as exc:
                # Exception bodies may contain signed request URLs or credentials.
                return dict(venue=venue, scope=scope, assets=[], error=type(exc).__name__)

    jobs = [(venue, scope, client) for scope, clients in (('Спот', spot), ('Фьючерсы', futures)) for venue, client in sorted(clients.items())]
    return await asyncio.gather(*(read(*job) for job in jobs))


def render(rows, limit=3700):
    out = ['💰 <b>Балансы · реальные счета</b>', 'Доступно / занято / всего · в единицах актива']
    if not rows:
        out.append('Биржи с ключами чтения не подключены.')
    for row in rows:
        block = f'<b>{escape(row["venue"].upper())} · {escape(row["scope"])}</b>'
        if row['error']:
            block += '\nНедоступно · ' + escape(row['error'])
        else:
            block += ''.join(f'\n{escape(str(a["asset"]))}: {amount(a["free"])} / {amount(a["used"])} / {amount(a["total"])}' for a in row['assets'])
        if len('\n\n'.join(out + [block])) > limit:
            out.append('Остальные активы не поместились в экран.')
            break
        out.append(block)
    out.append('Общие кошельки могут повторяться между разделами. Остатки не суммируются. — нет подтверждённых данных.')
    return '\n\n'.join(out)
