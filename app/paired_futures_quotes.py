"""Refresh both executable books before a paired Paper entry or mark."""
import asyncio
import time
import math
from .engine import Quote, evaluate, FEE_BPS
from .public_books import normalize
from .contract_book import to_base_levels
from .native_order_plan import prepare_pair
from .paired_futures_funding import attach


async def refresh(row, scanner, position=None):
    symbol, buy, sell = row['symbol'], row['buy'], row['sell']
    # Funding requests may take seconds on a cold cache. Do them before the
    # executable books, otherwise valid fresh books expire while funding loads.
    snapshots = None
    if not position:
        snapshots = await asyncio.gather(scanner.funding.get(buy, symbol),
                                         scanner.funding.get(sell, symbol))
    async def read(venue):
        start = time.time()
        client = scanner.clients[venue]
        # Paired entry preflight and open-position marks precede scanner REST
        # candidates in the shared MEXC depth budget. Test/replay clients retain
        # their existing fetch_order_book interface.
        if hasattr(client, 'fetch_priority_order_book'):
            request = client.fetch_priority_order_book(symbol, limit=20)
        else:
            request = client.fetch_order_book(symbol, limit=20)
        raw = await asyncio.wait_for(request, 2)
        book = normalize(raw, symbol, start, time.time(), 1.5, raw.get('data_source', 'REST'))
        stamp = float(book['timestamp']) / 1000 if book.get('timestamp') is not None else start
        size = scanner.specs[venue][symbol].contract_size
        return Quote(venue, symbol, to_base_levels(book['bids'], size),
                     to_base_levels(book['asks'], size), stamp,
                     received_at=raw.get('received_at', time.time()), data_source=raw.get('data_source', 'REST'))
    a, b = await asyncio.gather(read(buy), read(sell))
    qty = position.base_qty if position else scanner.notional / max(a.asks[0][0], b.bids[0][0])
    result = evaluate(a, b, scanner.notional, 1.5, base_qty=qty)
    if result is None:
        raise ValueError('PAIRED_BOOK_DEPTH_OR_AGE')
    native = prepare_pair(symbol, buy, sell, scanner.clients[buy], scanner.clients[sell],
                          qty, result['entry_buy'], result['entry_sell'])
    if not native.valid or (position and not math.isclose(native.base_qty, qty, rel_tol=1e-10)):
        raise ValueError('PAIRED_NATIVE_QUANTITY_INVALID')
    if not position:
        result = evaluate(a, b, scanner.notional, 1.5, base_qty=native.base_qty)
        if result is None:
            raise ValueError('PAIRED_BOOK_DEPTH_OR_AGE')
        if result['base_qty'] * max(result['entry_buy'],result['entry_sell']) > scanner.notional + 1e-8:
            raise ValueError('PAIRED_NOTIONAL_CAP_EXCEEDED')
    rates = []
    for venue in (buy, sell):
        market = scanner.clients[venue].market(symbol)
        try:
            rate = float(market.get('taker'))
        except (ValueError, TypeError):
            raise ValueError('PAIRED_PUBLIC_FEE_UNKNOWN') from None
        if not math.isfinite(rate) or not 0 <= rate < .05:
            raise ValueError('PAIRED_PUBLIC_FEE_UNKNOWN')
        rates.append(max(rate, FEE_BPS[venue] / 10000))
    result.update(native_plan=native.row(), safety_pct=scanner.safety_buffer_pct,
                  fee_pct=2 * sum(rates) * 100,
                  fee_source='CONSERVATIVE_PUBLIC_RATE_ASSUMPTION', decision_at=time.time())
    # Cost-weighted per-leg rates avoid averaging unequal-price legs.
    prices = (result['entry_buy'], result['entry_sell'], result['exit_buy'], result['exit_sell'])
    result['entry_fee_usd'] = result['base_qty'] * (prices[0]*rates[0] + prices[1]*rates[1])
    result['exit_fee_usd'] = result['base_qty'] * (prices[2]*rates[0] + prices[3]*rates[1])
    result['entry_fee_components']={buy:result['base_qty']*prices[0]*rates[0],sell:result['base_qty']*prices[1]*rates[1]}
    result['exit_fee_components']={buy:result['base_qty']*prices[2]*rates[0],sell:result['base_qty']*prices[3]*rates[1]}
    if not position:
        await attach(result, scanner.funding, scanner.hold_seconds, snapshots=snapshots)
    if time.time() - min(a.fetched, b.fetched) > 1.5:
        raise ValueError('PAIRED_BOOK_DEPTH_OR_AGE')
    return result
