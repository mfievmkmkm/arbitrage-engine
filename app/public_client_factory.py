from .exchange_names import public_exchange_class as exchange_class
from .public_books import wrap
import asyncio
import logging


async def build(ids, default_type=None):
    out = {}
    for name in ids:
        c = None
        logging.getLogger('arbitrage').info('Secondary market bootstrap: %s start', name)
        try:
            opts = {"enableRateLimit": True}
            if default_type:
                opts["options"] = {"defaultType": default_type}
            c = exchange_class(name)(opts)
            await asyncio.wait_for(c.load_markets(), 20)
            out[name] = wrap(c)
            logging.getLogger('arbitrage').info('Secondary market bootstrap: %s ready', name)
        except Exception as exc:
            logging.getLogger('arbitrage').warning(
                'Secondary market bootstrap: %s unavailable: %s', name, type(exc).__name__)
            try:
                if c is not None:
                    await c.close()
            except Exception:
                pass
    return out


async def close(clients):
    import asyncio

    await asyncio.gather(*(c.close() for c in clients.values()), return_exceptions=True)
