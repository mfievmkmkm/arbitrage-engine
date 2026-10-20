"""Compact read-only Telegram diagnostics and durable hourly no-trade reports."""
from html import escape
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button
from .market_signals import spot


class Service:
    interval=60
    def __init__(self,journal,capabilities=None):
        self.journal=journal;self.entry_enabled=True;self.capabilities=capabilities;self.last_capabilities=None
    async def cycle(self):
        await self.journal.no_trade_reports()
        report_interval=60 if getattr(self,'entry_report_count',0)<2 else 300
        if self.journal.clock()-getattr(self,'last_entry_report',0)>=report_interval:
            from .entry_evidence_report import inspect
            from .scan_audit import save
            import logging,json
            report=await inspect(self.journal)
            await save(self.journal.path,'entry_evidence',report)
            logging.getLogger('arbitrage').info('MARKET_ENTRY_EVIDENCE %s',json.dumps(report,ensure_ascii=False))
            self.last_entry_report=self.journal.clock()
            self.entry_report_count=getattr(self,'entry_report_count',0)+1
        if self.capabilities:
            from .scan_audit import save
            report=await self.capabilities()
            await save(self.journal.path,'execution',dict(ts=self.journal.clock(),**report))
            if report!=self.last_capabilities:
                import logging,json
                logging.getLogger('arbitrage').info('PRODUCT_EXECUTION_STATUS %s',json.dumps(report,ensure_ascii=False))
                self.last_capabilities=report
        return []


async def render(journal,bundle,data,controls=None,paused=False):
    feed=getattr(bundle,'market_signals',None)
    paired=getattr(bundle,'dex_paired',None)
    if data=='audit:why':
        from .entry_evidence_report import inspect,render as why_text
        text=why_text(await inspect(journal));rows=[]
    elif data=='audit:execution':
        from .product_execution_status import inspect,render as execution_text
        text=execution_text(await inspect(bundle,controls,paused));rows=[]
    elif data=='audit:comparison':
        text=await journal.comparison();rows=[]
    elif data=='audit:risk':
        a=controls.profile('paper') if controls else {}
        b=await feed.store.control() if feed else {}
        text=('🛡 <b>Риски · PAPER</b>\n\n'
              f'A · сжатие\nНа сторону ${a.get("notional_usd",0):g} · дневной стоп ${a.get("daily_loss_usd",0):g}\n'
              f'Стоп пары ${a.get("stop_loss_usd",0):g} · пар до {a.get("max_positions",0)}\n\n'
              f'B · перевод\nПокупка ${b.get("budget",0):g} · дневной стоп ${b.get("daily_loss",0):g}\n'
              f'Стоп теста ${b.get("total_loss",0):g}\n\n'
              +('⏸ Общая пауза входов' if paused else 'Новые входы разрешаются настройками каждой стратегии'))
        rows=[[button('Сжатие · параметры','ctl:risk'),button('Перевод · параметры','sig:settings')],
              [button('▶ Продолжить' if paused else '⏸ Пауза входов','resume' if paused else 'pause')]]
    elif data.startswith('audit:trace:'):
        text=await journal.trace(data.split(':',2)[2])
        rows=[]
    elif data=='audit:scanner':
        text='🔎 <b>Сканер</b>\n\n'
        if feed:
            now=feed.clock()
            for venue in ('bybit','mexc'):
                c=feed.clients.get(venue)
                markets=sum(spot(m) for m in c.markets.values()) if c else 0
                snap=feed.tickers.get(venue)
                quotes=len(snap[1]) if snap and 0<=now-snap[0]<=90 else 0
                text+=f'{venue.upper()} Spot · рынков {markets} · свежих тикеров {quotes}\n'
                if feed.health.get(venue) not in (None,'OK'):text+=escape(str(feed.health[venue])[:90])+'\n'
        service=getattr(getattr(bundle,'runtime',None),'services',{}).get('dex_paired')
        scan=getattr(service,'scanner',None)
        if scan:text+=f'\nDEX ↔ Futures · маршрутов {len(scan.catalog)} · проверок {scan.checked}'
        text+='\n\nТикеры находят кандидата; перед входом нужны свежие стаканы и полный расчёт.'
        rows=[[button('Сжатие','pair:scan'),button('Перевод','sig:details')]]
    else:
        hours=24 if data=='audit:day' else 1
        text=await journal.render(hours)
        s=await journal.snapshot(hours)
        rows=[[button(f'{r[1]} · {r[2]} · {r[5]}','audit:trace:'+r[0])] for r in s['latest'][:3]]
        rows.append([button('За час','audit:show'),button('За сутки','audit:day')])
        rows.append([button('Сравнение A / B','audit:comparison')])
        rows.append([button('Исполнение','audit:execution')])
        rows.append([button('Почему нет входов','audit:why')])
    rows.append([button('↻ Обновить',data),button('‹ Назад','home')])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)
