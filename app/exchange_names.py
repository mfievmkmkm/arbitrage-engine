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
