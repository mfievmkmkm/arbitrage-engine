import asyncio
from types import SimpleNamespace

import pytest

from app.dex_cex_identity import Gate

ASSET = '0x' + '1' * 40
ROUTE = dict(base='X', cex_venue='bybit', chain_id=1, asset=ASSET)


def currency(address=ASSET):
    return dict(code='X', networks={'ERC20': dict(contract=address)})


def client(response=None, existing=None):
    async def fetch():
        if isinstance(response, Exception):
            raise response
        return response
    return SimpleNamespace(has={'fetchCurrencies': True},
                           fetch_currencies=fetch, currencies=existing or {})


@pytest.mark.parametrize('first', [{}, {'Y': dict(code='Y')},
                                    {'X': dict(code='X', networks={})},
                                    RuntimeError('unavailable')])
def test_incomplete_first_source_does_not_hide_verified_second_source(first):
    async def run():
        gate = Gate({'bybit': client({'X': currency()})},
                    {'bybit': client(first)}, lambda: 1000)
        assert (await gate.verify(ROUTE))['asset'] == ASSET
    asyncio.run(run())


def test_empty_fetch_keeps_loaded_currency_networks():
    async def run():
        gate = Gate({'bybit': client({}, {'X': currency()})}, {}, lambda: 1000)
        assert (await gate.verify(ROUTE))['asset'] == ASSET
    asyncio.run(run())


def test_conflicting_sources_cannot_authorize_identity():
    async def run():
        gate = Gate({'bybit': client({'X': currency()})},
                    {'bybit': client({'X': currency('0x' + '2' * 40)})}, lambda: 1000)
        with pytest.raises(ValueError, match='DEX_CEX_IDENTITY_UNVERIFIED'):
            await gate.verify(ROUTE)
    asyncio.run(run())


def test_venue_cache_contains_metadata_for_subsequent_routes():
    async def run():
        gate = Gate({'bybit': client({'X': currency()})},
                    {'bybit': client({'Y': dict(code='Y')})}, lambda: 1000)
        with pytest.raises(ValueError, match='IDENTITY_UNVERIFIED'):
            await gate.verify(dict(ROUTE, base='Y'))
        assert (await gate.verify(ROUTE))['asset'] == ASSET
    asyncio.run(run())
