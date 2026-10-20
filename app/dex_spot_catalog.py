"""Independent B spot universe. No futures listing or transaction authority."""
import asyncio
from .dex_cex_identity import ALIASES, matching_contract
from .dex_transfer_source import STABLES
from .dex_request_budget import background
from .market_signals import spot
from .zerox_research import ADDRESS


class Catalog:
    enabled_name='market_signals'
    interval=10

    def __init__(self,router):
        self.router=router;self.entry_enabled=True;self.cursor={};self.last_log=0
        self.checked=0;self.rejected=0;self.last_reason=None;self.possible={};self.last_stage=None;self.last_venue=None

    @staticmethod
    def possibilities(client,rows):
        out=[]
        for symbol,m in sorted(client.markets.items()):
            if not spot(m):continue
            base=m['base'];currency=rows.get(base,{})
            for name,n in (currency.get('networks') or {}).items():
                if not isinstance(n,dict):continue
                raw=n.get('info') or {}
                chains={ALIASES.get(str(v).upper()) for v in (name,n.get('network'),n.get('id'),raw.get('chain'))}-{None}
                explicit=n.get('chainId',raw.get('chainId'))
                if explicit is not None:
                    try:chains.add(int(explicit))
                    except (TypeError,ValueError):continue
                if len(chains)!=1:continue
                chain=next(iter(chains))
                if chain not in STABLES:continue
                asset=n.get('contract') or n.get('contractAddress') or raw.get('contractAddress') or raw.get('contract')
                if not isinstance(asset,str) or not ADDRESS.fullmatch(asset):continue
                if asset.lower()==STABLES[chain][0]:continue
                if matching_contract(currency,dict(base=base,chain_id=chain,asset=asset)):
                    item=(symbol,base,chain,asset.lower())
                    if item not in out:out.append(item)
        return out

    async def cycle(self):
        from .scan_audit import save
        feed=self.router.feed
        for venue in sorted(feed.trade_venues & self.router.handlers.keys()):
            handler=self.router.handlers[venue];client=feed.clients.get(venue)
            if client is None:continue
            stage='CURRENCY_METADATA'
            try:
                rows=await feed.currencies(venue)
                stage='MATCH_SPOT_NETWORK_CONTRACT'
                possibilities=self.possibilities(client,rows);self.possible[venue]=len(possibilities)
                # Remove stale contracts from discovery; existing positions keep
                # their saved route and must independently recheck networks.
                current={(base,chain,asset) for _,base,chain,asset in possibilities}
                handler.independent_routes={k:r for k,r in handler.independent_routes.items()
                    if (r['base'],r['chain_id'],r['asset'].lower()) in current}
                token=background.set(True)
                try:
                    for _ in range(min(2,len(possibilities))):
                        symbol,base,chain,asset=possibilities[self.cursor.get(venue,0)%len(possibilities)]
                        self.cursor[venue]=self.cursor.get(venue,0)+1
                        key=(symbol,chain,asset)
                        if key in handler.independent_routes:continue
                        if len(handler.independent_routes)>=256:break
                        self.checked+=1
                        try:
                            provider=getattr(handler.quote.provider,'evm',handler.quote.provider)
                            network='eth' if chain==1 else 'bsc'
                            stage='VERIFY_ONCHAIN_TOKEN'
                            data=await asyncio.wait_for(provider.token(network,asset),8)
                            decimals=data.get('decimals')
                            if data.get('symbol','').upper()!=base or type(decimals) is not int or not 0<=decimals<=36:
                                raise ValueError('DEX_SPOT_CATALOG_METADATA_MISMATCH')
                            stable,stable_decimals,native=STABLES[chain]
                            handler.independent_routes[key]=dict(base=base,chain_id=chain,asset=asset,
                                asset_decimals=decimals,stable=stable,stable_decimals=stable_decimals,
                                stable_symbol='USDT',cex_venue=venue,cex_symbol=symbol,
                                native_symbol=native+'/USDT',native_decimals=18,
                                mode='paired',notional_usd=100,max_notional_usd=100,
                                identity_source='ACCOUNT_SPOT_NETWORK_AND_TOKEN_METADATA',
                                discovery_source='INDEPENDENT_SPOT_UNIVERSE',
                                identity_verified_at=feed.clock())
                            self.last_reason=None;self.last_stage=None;self.last_venue=None
                        except asyncio.CancelledError:raise
                        except Exception as exc:
                            self.rejected+=1
                            self.last_reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
                            self.last_stage=stage;self.last_venue=venue
                finally:background.reset(token)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.last_reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
                self.last_stage=stage;self.last_venue=venue
            await save(feed.journal.path,'spot_catalog_'+venue,dict(ts=feed.clock(),
                possible=self.possible.get(venue,0),checked=self.checked,rejected=self.rejected,
                failure_stage=self.last_stage,failure_venue=self.last_venue,
                routes=list(handler.independent_routes.values()),last_reason=self.last_reason))
        if feed.clock()-self.last_log>=60:
            import logging
            logging.getLogger('arbitrage').info('DEX_SPOT_UNIVERSE possible=%s routes=%s checked=%s reason=%s stage=%s venue=%s',
                self.possible,{v:len(h.independent_routes) for v,h in self.router.handlers.items()},self.checked,self.last_reason,self.last_stage,self.last_venue)
            self.last_log=feed.clock()
        return []
