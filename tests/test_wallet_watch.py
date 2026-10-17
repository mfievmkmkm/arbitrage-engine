import asyncio
import json
from types import SimpleNamespace as NS
import pytest
from app.wallet_watch import Reader,Store,address,quantity,render,token_list

WALLET='0x'+'1'*40


class FakeReader(Reader):
    def __init__(self,chain=1,ts=1000,bad_token=False):
        super().__init__(None,clock=lambda:1000)
        self.chain_id,self.ts,self.bad_token=chain,ts,bad_token
        self.calls=[]
    async def call(self,url,method,params):
        self.calls.append((method,params))
        if method=='eth_chainId':return hex(self.chain_id)
        if method=='eth_getBlockByNumber':return dict(number='0x64',timestamp=hex(self.ts))
        if method=='eth_getBalance':return hex(10**18)
        if self.bad_token:raise ValueError('RPC private-url secret')
        if params[0]['data']=='0x313ce567':return '0x'+f'{6:064x}'
        return '0x'+f'{1234567:064x}'


def test_same_network_block_exact_token_units_and_read_only():
    async def run():
        rpc=FakeReader()
        row=await rpc.chain(WALLET,1,[])
        assert not row['error'] and row['block']==100
        assert row['assets'][0]['raw']==str(10**18)
        assert row['assets'][1]['raw']=='1234567'
        text=render(WALLET,[row])
        assert 'ETH: 1' in text and ': 1.234567' in text
        assert 'secret' not in text
        for method,params in rpc.calls:
            assert method in Reader.READS
            if method in ('eth_getBalance','eth_call'):assert params[-1]=='0x64'
    asyncio.run(run())


@pytest.mark.parametrize('rpc',[lambda:FakeReader(chain=56),lambda:FakeReader(ts=800),lambda:FakeReader(ts=1001)])
def test_wrong_network_stale_or_future_block_is_unknown_not_zero(rpc):
    async def run():
        row=await rpc().chain(WALLET,1,[])
        assert row['error'] and row['assets']==[]
        assert 'Нет свежих данных' in render(WALLET,[row])
    asyncio.run(run())


def test_token_failure_preserves_native_balance_and_unknown_token():
    async def run():
        row=await FakeReader(bad_token=True).chain(WALLET,1,[])
        assert not row['error'] and row['assets'][1]['raw'] is None
        assert ': —' in render(WALLET,[row])
    asyncio.run(run())


def test_store_runtime_precedence_address_validation_and_exact_numbers(tmp_path,monkeypatch):
    monkeypatch.setenv('DEX_WATCH_WALLET_ADDRESS',WALLET)
    store=Store(tmp_path/'wallet.json')
    assert store.get()==WALLET
    other='0x'+'2'*40
    store.set(other)
    assert store.get()==other
    assert set(json.loads(store.path.read_text()))=={'address'}
    for bad in ('0x'+'0'*40,'0x123','seed words here','0x15698308B4F47459Dd25b42A6E419235F6208987'):
        with pytest.raises(ValueError):store.set(bad)
    assert quantity('1',36)=='0.'+'0'*35+'1'
    assert quantity(str(2**256-1),0)==str(2**256-1)
    assert quantity('0',18)=='0'


def test_token_scope_bound_and_deduplication():
    routes=[dict(chain_id=1,asset='0x'+f'{i:040x}',base='X',stable='0x'+'4'*40,stable_symbol='USDT') for i in range(1,40)]
    routes.append(dict(chain_id=56,asset='0x'+'5'*40,base='WRONG'))
    tokens=token_list(1,routes)
    assert len(tokens)==12 and not any(t['symbol']=='WRONG' for t in tokens)
    assert len({t['asset'].lower() for t in tokens})==12


def test_observer_cannot_submit_or_approve_even_if_called_directly():
    async def run():
        reader=Reader(None)
        for method in ('eth_sendRawTransaction','eth_sendTransaction','personal_sign','eth_sign'):
            with pytest.raises(ValueError,match='READ_ONLY'):
                await reader.call('https://example.org',method,[])
    asyncio.run(run())


def test_only_authorized_pending_chat_can_replace_watch_address(tmp_path,monkeypatch):
    import app.main as main
    store=Store(tmp_path/'wallet.json')
    edits=[]
    class Bot:
        async def edit_message_text(self,*args,**kwargs):edits.append((args,kwargs))
    monkeypatch.setattr(main,'wallet_watch_store',store)
    monkeypatch.setattr(main,'wallet_watch_pending',{1:(10,42,main.time.time()+120)})
    monkeypatch.setattr(main,'allowed',lambda ident:ident==1)
    async def run():
        for ident,chat in ((2,10),(1,11)):
            await main.wallet_watch_address(NS(from_user=NS(id=ident),chat=NS(id=chat),text=WALLET,bot=Bot()))
            assert not store.path.exists()
        await main.wallet_watch_address(NS(from_user=NS(id=1),chat=NS(id=10),text=WALLET,bot=Bot()))
        assert store.get()==WALLET and main.wallet_watch_pending=={}
        assert edits[0][1]['chat_id']==10 and edits[0][1]['message_id']==42
    asyncio.run(run())
