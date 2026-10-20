"""Address-only EVM balance display. No signer, approval, transaction or trading authority."""
import asyncio
import json
import os
import re
import time
from html import escape
from pathlib import Path
import aiohttp
from eth_utils import is_checksum_address, to_checksum_address
from .dex_network_registry import STABLES

NETWORKS = {
    1: ('Ethereum', 'ETH', 'https://ethereum-rpc.publicnode.com'),
    8453: ('Base', 'ETH', 'https://mainnet.base.org'),
    56: ('BSC', 'BNB', 'https://bsc-rpc.publicnode.com'),
    42161: ('Arbitrum', 'ETH', 'https://arb1.arbitrum.io/rpc'),
}
ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')


def address(value):
    if not isinstance(value, str) or not ADDRESS.fullmatch(value) or int(value[2:],16)==0:
        raise ValueError('Некорректный EVM-адрес')
    body=value[2:]
    if not (body.islower() or body.isupper()) and not is_checksum_address(value):
        raise ValueError('Не совпадает контрольная сумма адреса')
    return to_checksum_address(value)


class Store:
    def __init__(self, path): self.path=Path(path)

    def get(self):
        if self.path.exists():
            return address(json.loads(self.path.read_text())['address'])
        value=os.getenv('DEX_WATCH_WALLET_ADDRESS','').strip()
        return address(value) if value else None

    def set(self, value):
        value=address(value)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix(self.path.suffix+'.tmp')
        temp.write_text(json.dumps(dict(address=value)),encoding='utf-8')
        temp.replace(self.path)
        return value


def uint(value, word=False):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]+',value):
        raise ValueError('RPC_DATA_INVALID')
    if word and len(value)!=66:
        raise ValueError('RPC_WORD_INVALID')
    result=int(value,16)
    if result >= 2**256:
        raise ValueError('RPC_UINT_INVALID')
    return result


def token_list(chain, routes):
    # Native + the registry stable + exact contracts used by the product scanner.
    # This is not a complete index of every token ever sent to the address.
    stable=STABLES[chain][1]
    result={stable.lower():dict(asset=stable,symbol='USDT' if chain==56 else 'USDC')}
    for r in routes:
        if r.get('chain_id')!=chain:continue
        for key,symbol in (('asset',r.get('base')),('stable',r.get('stable_symbol','USDC'))):
            asset=r.get(key)
            if isinstance(asset,str) and ADDRESS.fullmatch(asset):
                result.setdefault(asset.lower(),dict(asset=asset,symbol=str(symbol or 'TOKEN')))
    return list(result.values())[:12]


class Reader:
    READS={'eth_chainId','eth_getBlockByNumber','eth_getBalance','eth_call'}

    def __init__(self, session, clock=time.time):
        self.session,self.clock=session,clock
        self.counter=0
        self.slots=asyncio.Semaphore(8)

    async def call(self, url, method, params):
        if method not in self.READS:raise ValueError('READ_ONLY_METHOD_REQUIRED')
        if not isinstance(url,str) or not url.startswith('https://'):raise ValueError('HTTPS_REQUIRED')
        self.counter+=1;ident=self.counter
        async with self.slots:
            async with self.session.post(url,json=dict(jsonrpc='2.0',id=ident,method=method,params=params),
                                         timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status!=200:raise ValueError('RPC_UNAVAILABLE')
                row=await response.json()
        if not isinstance(row,dict) or type(row.get('id')) is not int or row['id']!=ident or row.get('error') or 'result' not in row:
            raise ValueError('RPC_RESPONSE_INVALID')
        return row['result']

    async def chain(self, wallet, chain, routes):
        name,native,default=NETWORKS[chain]
        row=dict(chain_id=chain,name=name,native=native,assets=[],error=None)
        try:
            wallet=address(wallet)
            url=os.getenv(f'DEX_WATCH_RPC_{chain}',default)
            reported,block=await asyncio.gather(
                self.call(url,'eth_chainId',[]),
                self.call(url,'eth_getBlockByNumber',['latest',False]))
            if uint(reported)!=chain:
                raise ValueError('RPC_CHAIN_MISMATCH')
            number=uint(block['number']);ts=uint(block['timestamp'])
            if not 0<=self.clock()-ts<=120:raise ValueError('RPC_BLOCK_STALE')
            tag=hex(number)
            async def gas_balance():
                try:
                    amount=uint(await self.call(url,'eth_getBalance',[wallet,tag]))
                    return dict(symbol=native,raw=str(amount),decimals=18,error=None)
                except asyncio.CancelledError:raise
                except Exception:
                    return dict(symbol=native,raw=None,decimals=18,error='Нет данных')
            async def token(item):
                asset=item['asset']
                try:
                    decimals,balance=await asyncio.gather(
                        self.call(url,'eth_call',[dict(to=asset,data='0x313ce567'),tag]),
                        self.call(url,'eth_call',[dict(to=asset,data='0x70a08231'+wallet[2:].lower().rjust(64,'0')),tag]))
                    decimals=uint(decimals,True)
                    if decimals>36:raise ValueError('TOKEN_DECIMALS_INVALID')
                    return dict(symbol=item['symbol'],contract=asset,raw=str(uint(balance,True)),decimals=decimals,error=None)
                except asyncio.CancelledError:raise
                except Exception:
                    return dict(symbol=item['symbol'],contract=asset,raw=None,decimals=None,error='Нет данных')
            row['assets']=await asyncio.gather(gas_balance(),*(token(t) for t in token_list(chain,routes)))
            row.update(block=number,block_ts=ts,read_at=self.clock())
            if not 0<=self.clock()-ts<=120:raise ValueError('RPC_BLOCK_STALE')
        except asyncio.CancelledError:raise
        except Exception:
            row.update(error='Нет свежих данных RPC',assets=[])
        return row


async def snapshot(wallet,routes,timeout=8):
    wallet=address(wallet)
    async with aiohttp.ClientSession(trust_env=True) as session:
        reader=Reader(session)
        async def read(chain):
            try:return await asyncio.wait_for(reader.chain(wallet,chain,routes),timeout)
            except asyncio.TimeoutError:
                return dict(chain_id=chain,name=NETWORKS[chain][0],assets=[],error='RPC не ответил вовремя')
        return await asyncio.gather(*(read(chain) for chain in NETWORKS))


def quantity(raw,decimals):
    digits=str(int(raw))
    if not decimals:return digits
    digits=digits.zfill(decimals+1)
    whole,fraction=digits[:-decimals],digits[-decimals:].rstrip('0')
    return whole+('.'+fraction if fraction else '')


def render(wallet, rows):
    out=['👛 <b>Кошелёк · только просмотр</b>',f'<code>{escape(wallet)}</code>']
    for row in rows:
        block=f'<b>{escape(row["name"])}</b>'
        if row['error']:
            block+='\n'+escape(row['error'])
        else:
            for item in row['assets']:
                if item['error']:value='—'
                else:value=quantity(item['raw'],item['decimals'])
                # Native and the registry stable stay visible even when zero.
                if item is not row['assets'][0] and item is not row['assets'][1] and value=='0':continue
                label=escape(item['symbol'])
                if item.get('contract'):label+=' · '+escape(item['contract'][-6:])
                line=f'\n{label}: {value}'
                if len(block)+len(line)>700:
                    block+='\nЕщё токены не поместились.';break
                block+=line
            block+=f'\nБлок {row["block"]}'
        out.append(block)
    out.append('Монета газа и токены списка сканера · до 12 контрактов на сеть.\n— нет данных. PAPER-баланс отдельно.')
    return '\n\n'.join(out)
