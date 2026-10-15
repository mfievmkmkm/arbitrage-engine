"""Persist per-leg calendars and settle public history for two-leg Paper."""
import asyncio
import math
import time
from .funding_paper_source import Source, History


async def attach(row, service, hold_seconds=1200, snapshots=None):
    try:
        now = row.get('decision_at', row.get('ts', time.time()))
        if snapshots is None:
            snapshots = await asyncio.gather(service.get(row['buy'], row['symbol']), service.get(row['sell'], row['symbol']))
        calendar, reserve = {}, 0
        for prefix, sign, price, snap in zip(('long', 'short'), (-1, 1), (row['entry_buy'], row['entry_sell']), snapshots):
            first, hours, rate = float(snap.next_ts) / 1000, float(snap.interval_hours), float(snap.rate)
            if not all(math.isfinite(x) for x in (first, hours, rate)) or first <= now or not 0 < hours <= 24 or abs(rate) >= 1:
                raise ValueError('PAIRED_FUNDING_CALENDAR_UNKNOWN')
            calendar.update({prefix + '_next': first, prefix + '_interval': hours})
            count = max(0, int((now + hold_seconds - first) // (hours * 3600)) + 1)
            reserve += max(0, -sign * rate) * count * row['base_qty'] * price
        row.update(funding_calendar=calendar, funding_reserve_usd=reserve, funding_known=True)
    except (ValueError, TypeError, AttributeError):
        row.update(funding_calendar=None, funding_known=False, funding_reserve_usd=0)
    return row


class Settlements:
    def __init__(self, clients, service, notional):
        self.source = Source(clients, service, notional)

    async def __call__(self, position, until):
        calendar = position.funding_calendar or {}
        if set(calendar) != {'long_next', 'short_next', 'long_interval', 'short_interval'}:
            return History(False, 0, (), 'PAIRED_FUNDING_CALENDAR_UNKNOWN', position.opened_at)
        if (not math.isfinite(until) or until < position.opened_at
                or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in calendar.values())
                or any(calendar[prefix+'_next'] <= position.opened_at
                       or not 0 < calendar[prefix+'_interval'] <= 24 for prefix in ('long','short'))):
            return History(False, 0, (), 'PAIRED_FUNDING_CALENDAR_INVALID', position.opened_at)
        if until < min(calendar['long_next'], calendar['short_next']):
            return History(True, 0, (), 'NO_SETTLEMENT_DUE', until)
        row = dict(symbol=position.symbol, buy=position.buy, sell=position.sell,
                   base_qty=position.base_qty, entry_buy=position.entry_buy,
                   entry_sell=position.entry_sell, opened_at=position.opened_at, **calendar)
        try:
            return await asyncio.wait_for(self.source.settlements(row, until), 2)
        except asyncio.TimeoutError:
            return History(False, 0, (), 'FUNDING_HISTORY_TIMEOUT', position.opened_at)
