"""Rotate exact CEX network contracts into the read-only EVM scan catalog."""
import asyncio
from .dex_cex_identity import ALIASES, matching_contract
from .dex_network_registry import stable_config
from .zerox_research import ADDRESS
from .build_virtual_watchlist import eligible_markets
from .dex_request_budget import background


class Catalog:
    enabled_name = 'dex_paired'
    interval = 10

    def __init__(self, scanner):
        self.scanner = scanner
        self.entry_enabled = True
        self.index = {'bybit':0,'mexc':0}
        self.discovered = 0
        self.rejected = 0
        self.last_reason = None

    async def cycle(self):
        from .dex_paired_experiment import NATIVE
        source = self.scanner.service.paper.source
        for venue in ('bybit','mexc'):
            await self.scan_venue(source,venue,NATIVE)
        return []

    async def resolve_signal(self,venue,signal):
        """Exact channel contract -> independently verified executable route."""
        from .dex_paired_experiment import NATIVE
        source=self.scanner.service.paper.source
        chain,base,asset=signal['chain_id'],signal['base'],signal['asset']
        if chain not in NATIVE:raise ValueError('PAIRED_SIGNAL_CHAIN_UNSUPPORTED')
        client=source.public.get(venue)
        symbols=eligible_markets(client,base) if client else []
        if len(symbols)!=1:raise ValueError('PAIRED_SIGNAL_FUTURES_MARKET_UNAVAILABLE')
        await source.identity_gate.verify(dict(cex_venue=venue,base=base,chain_id=chain,asset=asset))
        network,stable_address=stable_config(chain)
        provider=source.provider.evm
        token,stable=await asyncio.gather(provider.token(network,asset),provider.token(network,stable_address))
        if token.get('symbol','').upper()!=base or stable.get('symbol','').upper() not in ('USDC','USDT'):
            raise ValueError('DEX_CATALOG_TOKEN_SCOPE_MISMATCH')
        return dict(chain_id=chain,base=base,asset=asset,stable=stable_address,
                    stable_symbol=stable['symbol'].upper(),asset_decimals=token['decimals'],stable_decimals=stable['decimals'],
                    cex_venue=venue,cex_symbol=symbols[0],native_symbol=NATIVE[chain]+'/USDT:USDT',native_decimals=18,
                    mode='paired',notional_usd=100,max_notional_usd=110,label=f'{base} · {venue.upper()} · {network}',
                    identity_source='EXCHANGE_NETWORK_CONTRACT_AND_TOKEN_METADATA')

    async def scan_venue(self,source,venue,NATIVE):
        seeds = [r for r in source.routes if r['cex_venue']==venue]
        if not seeds:
            legacy=getattr(self.scanner.service,'legacy',{}).get('dex_channel' if venue=='mexc' else 'dex_bybit')
            if legacy:seeds=[r for r in legacy.source.routes if r['cex_venue']==venue]
        if not seeds:return
        gate = source.identity_gate
        # Loads/refreshes scoped metadata even if the seed's identity is missing.
        try:
            await gate.verify(seeds[0])
        except ValueError:
            pass
        cached = gate.cache.get(venue)
        if not cached or not cached[2]:
            self.last_reason = 'DEX_CEX_METADATA_UNAVAILABLE'
            return
        client = source.public.get(venue)
        possibilities = []
        for base, currency in sorted(cached[1].items()):
            if not client or len(eligible_markets(client, base))!=1:
                continue
            for name, network in (currency.get('networks') or {}).items():
                if not isinstance(network, dict):
                    continue
                info = network.get('info') or {}
                chains = {ALIASES.get(str(n).upper()) for n in
                          (name, network.get('network'), network.get('id'), info.get('chain'))} - {None}
                raw_chain = network.get('chainId', info.get('chainId'))
                if raw_chain is not None:
                    try:
                        chains.add(int(raw_chain))
                    except (TypeError, ValueError):
                        continue
                if len(chains)!=1:
                    continue
                chain = next(iter(chains))
                if chain not in NATIVE:
                    continue
                asset = network.get('contract') or network.get('contractAddress') or info.get('contractAddress') or info.get('contract')
                if not isinstance(asset, str) or not ADDRESS.fullmatch(asset):
                    continue
                probe = dict(chain_id=chain, asset=asset, base=base)
                if not matching_contract(currency, probe):
                    continue
                try:
                    stable_config(chain)
                except ValueError:
                    continue
                possibilities.append((base, chain, asset.lower()))
        possibilities = list(dict.fromkeys(possibilities))
        self.discovered = len(possibilities)
        known = {(r['cex_venue'],r['base'], r['chain_id'], r['asset'].lower()) for r in self.scanner.catalog.values()}
        token = background.set(True)
        try:
            for _ in range(min(4, len(possibilities))):
                base, chain, asset = possibilities[self.index[venue] % len(possibilities)]
                self.index[venue] += 1
                if (venue,base,chain,asset) in known or len(self.scanner.catalog)>=256:
                    continue
                try:
                    network, stable_address = stable_config(chain)
                    provider = source.provider.evm
                    data = await asyncio.wait_for(provider.token(network, asset), 8)
                    stable = await asyncio.wait_for(provider.token(network, stable_address), 8)
                    if data['symbol'].upper()!=base or stable['symbol'].upper() not in ('USDC','USDT'):
                        raise ValueError('DEX_CATALOG_TOKEN_SCOPE_MISMATCH')
                    route = dict(chain_id=chain, asset=asset, stable=stable_address,
                                 base=base, stable_symbol=stable['symbol'].upper(),
                                 asset_decimals=data['decimals'], stable_decimals=stable['decimals'],
                                 cex_venue=venue, cex_symbol=eligible_markets(client,base)[0],
                                 native_symbol=NATIVE[chain]+'/USDT:USDT', native_decimals=18,
                                 mode='paired', notional_usd=100, max_notional_usd=110,
                                 label=f'{base} · {venue.upper()} · {network}',
                                 identity_source='EXCHANGE_NETWORK_CONTRACT_AND_TOKEN_METADATA')
                    self.scanner.add(route)
                    known.add((venue,base,chain,asset))
                    self.last_reason = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.rejected += 1
                    self.last_reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        finally:
            background.reset(token)
