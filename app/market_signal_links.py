"""Allowlisted exchange links. Unsupported routes link to the venue homepage."""
from urllib.parse import quote, urlsplit


def market_link(venue, base, market=None):
    b = quote(base, safe='')
    templates = {
        'binance': 'https://www.binance.com/en/trade/{b}_USDT?type=spot',
        'bybit': 'https://www.bybit.com/en/trade/spot/{b}/USDT',
        'gateio': 'https://www.gate.com/trade/{b}_USDT',
        'gate': 'https://www.gate.com/trade/{b}_USDT',
        'mexc': 'https://www.mexc.com/exchange/{b}_USDT',
        'bitget': 'https://www.bitget.com/spot/{b}USDT',
        'okx': 'https://www.okx.com/trade-spot/{low}-usdt',
        'kucoin': 'https://www.kucoin.com/trade/{b}-USDT',
        'htx': 'https://www.htx.com/trade/{low}_usdt?type=spot',
        'coinex': 'https://www.coinex.com/en/exchange/{low}-usdt',
        'bitmart': 'https://www.bitmart.com/trade/en-US?symbol={b}_USDT',
    }
    if venue in templates:
        return templates[venue].format(b=b, low=b.lower()), True
    url = market if isinstance(market, str) else ''
    parsed = urlsplit(url)
    if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
        return url, False
    return None, False
