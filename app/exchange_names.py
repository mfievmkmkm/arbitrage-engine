"""Keep configured venue identities stable across CCXT class renames."""

import ccxt.async_support as ccxt

ALIASES = {"gateio": "gate"}


def exchange_class(name):
    cls = getattr(ccxt, name, None)
    if cls is None:
        cls = getattr(ccxt, ALIASES.get(name, name), None)
    if cls is None:
        raise ValueError("EXCHANGE_UNSUPPORTED:" + name)
    return cls


def public_exchange_class(name):
    from .config import config
    if not config.public_streams:
        return exchange_class(name)
    import ccxt.pro as pro
    cls = getattr(pro, name, None) or getattr(pro, ALIASES.get(name, name), None)
    return cls or exchange_class(name)
