"""Require exchange currency network metadata; a ticker match is not proof."""
import asyncio
from .dex_network_registry import CHAINS
from .jupiter_research import mint_valid
from .zerox_research import ADDRESS

ALIASES = dict(CHAINS, ERC20=1, BEP20=56, BSC=56, SOL='solana',
               ARB=42161, ARBONE=42161, MATIC=137, AVAXC=43114)


def matching_contract(currency, route):
    if not isinstance(currency, dict) or currency.get('code') != route['base']:
        return False
    rows = []
    for key, network in (currency.get('networks') or {}).items():
        if not isinstance(network, dict):
            continue
        info = network.get('info') or {}
        chain = network.get('chainId', info.get('chainId'))
        try:
            chain = int(chain) if chain is not None else None
        except (ValueError, TypeError):
            chain = None
        names = [key, network.get('network'), network.get('id'), info.get('chain'), info.get('network')]
        known = {ALIASES.get(str(x).upper()) for x in names if x is not None} - {None}
        if chain is not None:
            known.add(chain)
        if known != {route['chain_id']}:
            continue
        address = network.get('contract') or network.get('contractAddress') or info.get('contractAddress') or info.get('contract')
        if (not address and currency['code']=='SOL' and route['chain_id']=='solana'
                and route['asset']=='So11111111111111111111111111111111111111112'):
            # Exchange native SOL corresponds to the canonical wrapped-SOL DEX mint.
            rows.append(route['asset'])
            continue
        if not isinstance(address, str):
            continue
        valid = mint_valid(address) if route['chain_id']=='solana' else ADDRESS.fullmatch(address)
        if valid:
            rows.append(address if route['chain_id']=='solana' else address.lower())
    expected = route['asset'] if route['chain_id']=='solana' else route['asset'].lower()
    return bool(rows) and set(rows) == {expected}


class Gate:
    def __init__(self, public, private, clock):
        self.public, self.private, self.clock = public, private, clock
        self.cache = {}

    async def verify(self, route):
        venue = route['cex_venue']
        cached = self.cache.get(venue)
        if not cached or self.clock()-cached[0] > 300:
            data = None
            for client in (self.private.get(venue), self.public.get(venue)):
                if client is None:
                    continue
                if getattr(client, 'has', {}).get('fetchCurrencies') is True:
                    try:
                        data = await asyncio.wait_for(client.fetch_currencies(), 3)
                        if isinstance(data, dict):
                            break
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
                existing = getattr(client, 'currencies', {})
                if matching_contract(existing.get(route['base']), route):
                    data = existing
                    break
            self.cache[venue] = (self.clock(), data or {})
            cached = self.cache[venue]
        if not matching_contract(cached[1].get(route['base']), route):
            raise ValueError('DEX_CEX_IDENTITY_UNVERIFIED')
        return dict(source='EXCHANGE_CURRENCY_NETWORK_METADATA', chain_id=route['chain_id'],
                    asset=route['asset'], base=route['base'], verified_at=self.clock())
