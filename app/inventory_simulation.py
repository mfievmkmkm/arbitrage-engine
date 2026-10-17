"""Explicit synthetic execution demo on isolated ephemeral accounts.

Not market evidence, not added to the user's Paper wallet or profitability.
"""
import tempfile
from pathlib import Path
from .inventory_paper import Engine


def book(venue,ts,ask,bid,size='100'):
    return dict(venue=venue,asset='DEMO',ts=str(ts),ask=str(ask),bid=str(bid),
        asks=[[str(ask),size]],bids=[[str(bid),size]],step='.001',min_qty='.001',min_notional='1',fee_rate='.002')


async def run():
    results=[]
    with tempfile.TemporaryDirectory(prefix='inventory-demo-') as directory:
        for index,name in enumerate(('Обе стороны исполнились','Вторая сторона не исполнилась','Вторая сторона исполнилась частично')):
            clock=[1000.0];a=book('A',1000,100,99.9);b=book('B',1000,106.1,106)
            rb=dict(verified=True,ts='1000',base=dict(available=True,network='DEMO',fee='.001',minimum='.001',delay_seconds='900'),
                    quote=dict(available=True,network='DEMO_USDT',fee='.05',minimum='1',delay_seconds='900'))
            snapshot=dict(asset='DEMO',buy='A',sell='B',ts='1000',skew='0',identity_verified=True,
                          books=[a,b],wallet_books={'A|DEMO':a,'B|DEMO':b},rebalance=rb)
            engine=Engine(Path(directory)/f'{index}.db',clock=lambda:clock[0]);await engine.init()
            await engine.seed([snapshot]);await engine.mark(snapshot['wallet_books'])
            second=b if index==0 else book('B',1000,91,90) if index==1 else book('B',1000,106.1,106,'.125')
            pos=await engine.execute(snapshot,a,second,a)
            class Transfers:
                async def network_check(self,*args):return True
            clock[0]=1901;await engine.monitor(Transfers())
            results.append(dict(name=name,net=engine.state['realized'],closed=engine.state['closed'],
                                fills=pos['fills'],status='CLOSED' if engine.state['closed'] else pos['status']))
    return results


def render(results):
    text='🧪 <b>Учебный прогон симулятора</b>\n\nСИНТЕТИЧЕСКИЕ цены 100 / 106 USDT, не текущий рынок. '
    text+='Три независимых счёта с одинаковыми расходами; результат не добавляется к твоему балансу.\n\n'
    for r in results:
        text+=f'{r["name"]}: {float(r["net"]):+.4f} USDT · {r["status"]}\n'
    return text+'\nЭто проверка механики, не доказательство доходности. Рыночные PAPER-входы используют реальные стаканы и отдельные проверки.'
