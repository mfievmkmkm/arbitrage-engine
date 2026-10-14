import asyncio
from app.dex_virtual_paper_source import route_key


def test_adaptive_size_rechecks_and_does_not_shrink_stale_books():
    from test_dex_virtual_paper import source
    async def go():
        s,r,_,_,now=source("reference")
        s.adaptive=True;r.update(notional_usd=100,max_notional_usd=100)
        calls=[]
        async def attempt(route):
            calls.append(route["notional_usd"])
            return dict(ok=False,reason="RECOVERY_BOOK_SLIPPAGE_TOO_HIGH")
        s._entry=attempt
        await s.entry(r);await s.entry(r);await s.entry(r);await s.entry(r)
        assert calls==[100,50,25,12.5]
        assert route_key(r) not in s.adaptive_sizes
        async def stale(route):
            calls.append(route["notional_usd"])
            return dict(ok=False,reason="RECOVERY_BOOK_STALE")
        s._entry=stale
        await s.entry(r);await s.entry(r)
        assert calls[-2:]==[100,100]
    asyncio.run(go())


def test_confirmation_keeps_reduced_size_but_expiry_resets():
    from test_dex_virtual_paper import source
    async def go():
        s,r,_,_,now=source("reference");s.adaptive=True
        r.update(notional_usd=100,max_notional_usd=100)
        calls=[]
        async def attempt(route):
            calls.append(route["notional_usd"])
            return dict(ok=False,reason="JUPITER_PRICE_IMPACT_LIMIT" if len(calls)==1 else "DEX_REFERENCE_CONFIRMATION_REQUIRED")
        s._entry=attempt
        await s.entry(r);now[0]+=5;await s.entry(r);now[0]+=5;await s.entry(r)
        assert calls==[100,50,50]
        now[0]+=61;await s.entry(r);assert calls[-1]==100
    asyncio.run(go())
