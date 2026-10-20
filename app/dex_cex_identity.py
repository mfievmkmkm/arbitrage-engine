"""Require exchange currency network metadata; a ticker match is not proof."""
import asyncio
from .dex_network_registry import CHAINS
from .jupiter_research import mint_valid
from .zerox_research import ADDRESS
from .evidence_error import EvidenceError

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
        if (not address and currency['code']=='ETH' and route['chain_id']==1
                and route['asset'].lower()=='0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'):
            # Canonical Ethereum WETH unwraps 1:1 into exchange native ETH.
            rows.append(route['asset'].lower())
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
        self.locks = {}
        self.registry = None

    async def verify(self, route):
        async with self.locks.setdefault(route['cex_venue'], asyncio.Lock()):
            return await self._verify(route)

    async def _verify(self, route):
        venue = route['cex_venue']
        cached = self.cache.get(venue)
        if not cached or not 0<=self.clock()-cached[0]<=300:
            data = {}
            errors = []
            # Collect all read-only sources. A nonempty currency response can
            # still omit this asset or its networks; it is not a reason to skip
            # the other client. Keep conflicting network evidence fail-closed.
            def collect(currencies):
                if not isinstance(currencies, dict):
                    return
                for code, currency in currencies.items():
                    if not isinstance(currency, dict):
                        continue
                    merged = data.setdefault(code, dict(currency, networks={}))
                    for key, network in (currency.get('networks') or {}).items():
                        if not isinstance(network, dict):
                            continue
                        networks = merged['networks']
                        name = key
                        while name in networks and networks[name] != network:
                            name = str(name) + '#'
                        # Preserve the network name when retaining conflicts.
                        networks[name] = dict(network, network=network.get('network') or key)

            seen = set()
            for client in (self.private.get(venue), self.public.get(venue)):
                if client is None or id(client) in seen:
                    continue
                seen.add(id(client))
                if getattr(client, 'has', {}).get('fetchCurrencies') is True:
                    try:
                        collect(await asyncio.wait_for(client.fetch_currencies(), 3))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                collect(getattr(client, 'currencies', {}))
            self.cache[venue] = (self.clock(), data, bool(data), tuple(errors))
            cached = self.cache[venue]
        if not cached[2]:
            raise ValueError('DEX_CEX_METADATA_UNAVAILABLE')
        if route['base'] not in cached[1]:
            raise ValueError('DEX_CEX_CURRENCY_NOT_LISTED')
        if not matching_contract(cached[1].get(route['base']), route):
            currency=cached[1].get(route['base']) or {}
            networks=[]
            for key,n in (currency.get('networks') or {}).items():
                if not isinstance(n,dict):continue
                info=n.get('info') or {}
                networks.append(dict(network=key,chain_id=n.get('chainId',info.get('chainId')),
                    contract=n.get('contract') or n.get('contractAddress') or info.get('contractAddress') or info.get('contract')))
            raise EvidenceError('DEX_CEX_IDENTITY_UNVERIFIED',venue=venue,base=route['base'],
                expected_chain=route['chain_id'],expected_contract=route['asset'],
                exchange_networks=networks,metadata_errors=cached[3],observed_at=cached[0])
        evidence=dict(source='EXCHANGE_CURRENCY_NETWORK_METADATA', chain_id=route['chain_id'],
                      asset=route['asset'], base=route['base'], verified_at=cached[0],expires_at=cached[0]+300)
        if self.registry and route.get('cex_symbol'):
            market=self.public[venue].markets.get(route['cex_symbol'],{})
            evidence=await self.registry.record(route,evidence,market)
        return evidence
