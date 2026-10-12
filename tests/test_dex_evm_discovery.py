import asyncio
from types import SimpleNamespace
import pytest
from app.dex_signal_focus import parse_signal
from app.dex_network_registry import CHAINS, stable_config
from app.dex_route_discovery import Discovery

ASSET='0x'+'2'*40

@pytest.mark.parametrize('network',list(CHAINS))
def test_network_parser(network):
    address='MukLDtJ8Cx9DxLbeyLRSWPSposTMWuwHANbuaudpump' if network=='SOLANA' else ASSET
    s=parse_signal(f'SHORT? #ZC\n#{network} Dep: green\n{address}')
    assert s['chain_id']==CHAINS[network]


def test_evm_exact_metadata_and_restart(tmp_path):
    async def go():
        network,stable=stable_config(8453)
        class EVM:
            api_key='test'
            async def token(self,n,a):
                assert n==network
                return dict(symbol='USDC' if a==stable else 'ZC',decimals=6 if a==stable else 18)
        class Client:
            markets={'ZC/USDT:USDT':dict(symbol='ZC/USDT:USDT',base='ZC',quote='USDT',settle='USDT',swap=True,linear=True,active=True,contractSize=1)}
        def engine(venue):
            return SimpleNamespace(clock=lambda:100,state={'experiment_config':{'venue':venue,'notional':100}},source=SimpleNamespace(routes=[],public={venue:Client()},provider=SimpleNamespace(evm=EVM())))
        engines={'dex_channel':engine('mexc'),'dex_bybit':engine('bybit')}
        d=Discovery(engines,tmp_path/'routes.json')
        await d.signal(f'SHORT? #ZC\n#BASE\n{ASSET}',99)
        for e in engines.values():
            assert e.source.routes[0]['chain_id']==8453
            assert e.source.routes[0]['asset']==ASSET and e.source.routes[0]['asset_decimals']==18
        async def idle():await asyncio.Event().wait()
        d.run=idle
        await d.start();assert len(engines['dex_channel'].source.routes)==1;await d.stop()
    asyncio.run(go())


def test_unknown_stable_is_not_guessed(monkeypatch):
    monkeypatch.delenv('DEX_EVM_NETWORKS_JSON',raising=False)
    with pytest.raises(ValueError,match='стейблкоина'):stable_config(4663)
