from .exchange_names import exchange_class
import asyncio


async def build(ids, default_type=None):
    out = {}
    for name in ids:
        c = None
        try:
            opts = {"enableRateLimit": True}
            if default_type:
                opts["options"] = {"defaultType": default_type}
            c = exchange_class(name)(opts)
            await asyncio.wait_for(c.load_markets(), 20)
            out[name] = c
        except Exception:
            try:
                await c.close()
            except Exception:
                pass
    return out


async def close(clients):
    import asyncio

    await asyncio.gather(*(c.close() for c in clients.values()), return_exceptions=True)
