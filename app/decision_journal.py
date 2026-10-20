"""Append-only candidate checks. No exchange, wallet or execution authority."""
import asyncio
import json
import time
import uuid
from html import escape
import aiosqlite
from .secret_redaction import redact
from .payload_codec import pack,loads as payload_loads

STAGES=('SIGNAL','MATCH','IDENTITY','NETWORK','QUOTES','COSTS','RISK','EXECUTOR','EXECUTED','CLOSED')
REMEDIES={
 'KYBER_FILTERED_LIQUIDITY':'Маршрут не найден среди разрешённых пулов; нужен другой проверяемый пул или меньший размер.',
 'DEX_TAX_SIMULATION_FAILED':'Симуляция токена не прошла; дождаться подтверждённой проверки или выбрать другой токен.',
 'DEX_TAX_HTTP_404':'Сервис не знает этот пул/токен; безопасность остаётся неподтверждённой.',
 'DEX_TAX_HONEYPOT_OR_UNKNOWN':'Не подтверждена возможность безопасной продажи токена.',
 'DEX_CEX_IDENTITY_UNVERIFIED':'Нужны совпадающие сеть и контракт в данных биржи.',
 'PAIRED_PARTIAL_NET_LOW':'Сценарный NET ниже заданного порога; числа находятся в проверке кандидата.',
 'DEX_VIRTUAL_REVERSE_PREFLIGHT_NET_STOP':'Затраты немедленного выхода превышают лимит риска; числа находятся в проверке.',
 'DEX_SHORT_OR_INVENTORY_UNAVAILABLE':'DEX дороже: нет подтверждённого запаса токена или механизма короткой продажи.',
 'CHANNEL_SIGNAL_EXPIRED':'Сигнал устарел; возможна только новая независимая проверка рынка.',
 'ALIGNED_IS_ANALYSIS_REPORT':'Отчёт Aligned сохранён для анализа и не запускает вход.',
}
SCHEMA='''
CREATE TABLE IF NOT EXISTS arb_candidates(
 id TEXT PRIMARY KEY, strategy TEXT NOT NULL, mode TEXT NOT NULL, source TEXT NOT NULL,
 symbol TEXT NOT NULL, buy TEXT NOT NULL, sell TEXT NOT NULL, detected REAL NOT NULL,
 updated REAL NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arb_candidates_time ON arb_candidates(detected,strategy,mode);
CREATE TABLE IF NOT EXISTS arb_checks(
 id INTEGER PRIMARY KEY, candidate_id TEXT NOT NULL, ts REAL NOT NULL,
 stage TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arb_checks_candidate ON arb_checks(candidate_id,id);
CREATE INDEX IF NOT EXISTS arb_checks_time ON arb_checks(ts,stage,status);
CREATE TABLE IF NOT EXISTS arb_lifecycle(
 id INTEGER PRIMARY KEY, candidate_id TEXT, strategy TEXT NOT NULL, mode TEXT NOT NULL,
 position_id TEXT NOT NULL, ts REAL NOT NULL, event TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS arb_lifecycle_position ON arb_lifecycle(strategy,position_id,id);
CREATE TABLE IF NOT EXISTS arb_config_events(
 id INTEGER PRIMARY KEY, ts REAL NOT NULL, strategy TEXT NOT NULL, before_payload TEXT NOT NULL, after_payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arb_diagnostic_reports(
 id INTEGER PRIMARY KEY,ts REAL NOT NULL,strategy TEXT NOT NULL,window_hours REAL NOT NULL,payload TEXT NOT NULL);
'''


def dump(value):
    return pack(json.dumps(redact(value),ensure_ascii=False,allow_nan=False,default=str))


def failure_stage(reason):
    r=reason.upper()
    if any(x in r for x in ('IDENTITY','CONTRACT','КОНТРАКТ','TOKEN_TAX','DEX_TAX','CURRENCY_NOT_LISTED')):return 'IDENTITY'
    if any(x in r for x in ('NETWORK','СЕТ','ДЕПОЗИТ','DEPOSIT','ВЫВОД','WITHDRAW','КЛЮЧ С ПРАВОМ')):return 'NETWORK'
    if any(x in r for x in ('RISK','STOP','BALANCE','CAPITAL','БАЛАНС','ЛИМИТ УБЫТКА','USDT','NOTIONAL','LIMIT','РАЗМЕР')):return 'RISK'
    if any(x in r for x in ('NET_LOW','СПРЕД','СТРЕСС','КОМИСС','FEE','FUNDING','ECONOMIC','GAP')):return 'COSTS'
    return 'QUOTES'


class Journal:
    def __init__(self,path,clock=time.time):
        self.path,self.clock=str(path),clock
        self.ready=False;self.lock=asyncio.Lock()

    async def init(self):
        if self.ready:return
        async with self.lock:
            if self.ready:return
            async with aiosqlite.connect(self.path) as db:
                await db.executescript(SCHEMA);await db.commit()
            self.ready=True

    async def start(self,strategy,symbol,buy,sell,mode='observe',source='scanner',evidence=None):
        await self.init();ident=uuid.uuid4().hex
        now=self.clock()
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO arb_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,strategy,mode,source,symbol,buy,sell,now,now,'CHECKING','',dump(evidence or {})))
            await self.append(db,ident,'SIGNAL','PASS','CANDIDATE_DETECTED',evidence or {})
            await db.commit()
        return ident

    async def append(self,db,ident,stage,status,reason,details=None):
        if stage not in STAGES or status not in ('PASS','FAIL','WAIT','INFO'):
            raise ValueError('JOURNAL_CHECK_INVALID')
        await db.execute('INSERT INTO arb_checks(candidate_id,ts,stage,status,reason,payload) VALUES(?,?,?,?,?,?)',
                         (ident,self.clock(),stage,status,reason,dump(details or {})))

    async def check(self,ident,stage,status,reason,details=None):
        async with aiosqlite.connect(self.path) as db:
            await self.append(db,ident,stage,status,reason,details);await db.commit()

    async def finish(self,ident,status,reason='',details=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE arb_candidates SET updated=?,status=?,reason=? WHERE id=?',
                             (self.clock(),status,reason,ident))
            if details is not None:
                await self.append(db,ident,failure_stage(reason),'FAIL' if status=='REJECTED' else 'INFO',reason,details)
            await db.commit()

    async def lifecycle(self,db,strategy,position,event,status,mode='paper'):
        """Called in the SAME transaction as the financial state update."""
        ident=position.get('trace_id')
        pid=str(position.get('id',''))
        if ident:
            row=await (await db.execute('SELECT mode FROM arb_candidates WHERE id=?',(ident,))).fetchone()
            if row:mode=row[0]
        await db.execute('INSERT INTO arb_lifecycle(candidate_id,strategy,mode,position_id,ts,event,status,payload) VALUES(?,?,?,?,?,?,?,?)',
                         (ident,strategy,mode,pid,self.clock(),event,status,dump(position)))
        if ident:
            stage='CLOSED' if status in ('CLOSED','CANCELLED_BEFORE_BUY','ABORTED_AFTER_FUNDING') else 'EXECUTED'
            if stage=='CLOSED' or event in ('DEX_PAPER_OPEN','PAIR_HEDGED','PARTIAL_HEDGE_RECOVERED','BUY_AND_BASE_RESERVATION','FUNDING_RESERVATION','DEX_TRANSFER_RESERVATION'):
                await self.append(db,ident,stage,'PASS',event,dict(position_id=pid,net=position.get('net'),status=status))
            state='CLOSED' if stage=='CLOSED' else ('RESERVED' if status=='ENTRY_RESERVED' else ('EXPOSURE' if status in ('HEDGE_PENDING','UNHEDGED_RECOVERY') else ('REJECTED' if status=='CANCELLED_BEFORE_FILL' else 'OPEN')))
            await db.execute('UPDATE arb_candidates SET updated=?,status=?,reason=? WHERE id=?',
                             (self.clock(),state,event,ident))

    async def snapshot(self,hours=1):
        await self.init();since=self.clock()-hours*3600
        async with aiosqlite.connect(self.path) as db:
            rows=await (await db.execute('SELECT strategy,mode,COUNT(*) FROM arb_candidates WHERE detected>=? GROUP BY strategy,mode',(since,))).fetchall()
            stages=await (await db.execute('SELECT c.strategy,k.stage,COUNT(DISTINCT c.id) FROM arb_checks k JOIN arb_candidates c ON c.id=k.candidate_id WHERE c.detected>=? AND k.status="PASS" GROUP BY c.strategy,k.stage',(since,))).fetchall()
            reasons=await (await db.execute('SELECT strategy,reason,COUNT(*) FROM arb_candidates WHERE detected>=? AND status="REJECTED" GROUP BY strategy,reason ORDER BY COUNT(*) DESC LIMIT 8',(since,))).fetchall()
            unresolved=await (await db.execute('SELECT COUNT(*) FROM arb_candidates WHERE status="CHECKING" AND updated<?',(self.clock()-60,))).fetchone()
            stale=await (await db.execute('SELECT id,strategy,symbol,buy,sell,status,reason,updated FROM arb_candidates ORDER BY detected DESC LIMIT 5')).fetchall()
        return dict(hours=hours,counts=rows,stages=stages,reasons=reasons,unfinished=unresolved[0],latest=stale)

    async def trace(self,ident):
        await self.init()
        async with aiosqlite.connect(self.path) as db:
            row=await (await db.execute('SELECT strategy,symbol,buy,sell,mode,source,status,reason FROM arb_candidates WHERE id=?',(ident,))).fetchone()
            checks=await (await db.execute('SELECT ts,stage,status,reason,payload FROM arb_checks WHERE candidate_id=? ORDER BY id DESC LIMIT 15',(ident,))).fetchall()
        if row is None:raise ValueError('Проверка не найдена')
        out=[f'🩺 <b>{escape(row[1])} · {row[0]}</b>',escape(f'{row[2]} → {row[3]} · {row[4]} · {row[5]}'),escape(f'{row[6]} · {row[7]}')]
        for ts,stage,status,reason,payload in reversed(checks):
            data=payload_loads(payload)
            facts={k:v for k,v in data.items() if k in ('observed','threshold','unit','partial_net_usd','costs_usd','raw_edge_usd','immediate_net_usd','reverse_loss_limit_usd','error_type')}
            numeric=('spread_pct','net','stress_net','net_after_reserve','budget','spent','partial_net','raw_edge','costs','funding_reserve','target_usd','stop_usd','min_spread','max_spread','min_net','stress_drop','gas')
            facts.update({k:v for k,v in data.items() if k in numeric})
            for group in ('economics','thresholds','plan'):
                values=data.get(group)
                if isinstance(values,dict):
                    chosen={k:v for k,v in values.items() if k in numeric and isinstance(v,(str,int,float,bool))}
                    if chosen:facts[group]=chosen
            # Bound plain facts before escaping; never truncate an HTML entity.
            block=escape(f'{stage} · {status} · {reason}')+ ('\n'+escape(json.dumps(facts,ensure_ascii=False)[:700]) if facts else '')
            if len('\n\n'.join(out))+len(block)+2>3900:break
            out.append(block)
        return '\n\n'.join(out)

    async def no_trade_reports(self,hours=1):
        s=await self.snapshot(hours)
        async with aiosqlite.connect(self.path) as db:
            for strategy in ('A','B'):
                counts=sum(n for st,mode,n in s['counts'] if st==strategy)
                opened=sum(n for st,stage,n in s['stages'] if st==strategy and stage=='EXECUTED')
                first=await (await db.execute('SELECT MIN(detected) FROM arb_candidates WHERE strategy=?',(strategy,))).fetchone()
                previous=await (await db.execute('SELECT MAX(ts) FROM arb_diagnostic_reports WHERE strategy=?',(strategy,))).fetchone()
                if not counts or opened or first[0] is None or self.clock()-first[0]<hours*3600:continue
                if previous[0] is not None and self.clock()-previous[0]<hours*3600:continue
                await db.execute('INSERT INTO arb_diagnostic_reports(ts,strategy,window_hours,payload) VALUES(?,?,?,?)',
                    (self.clock(),strategy,hours,dump(dict(snapshot=s,reason='NO_PAPER_EXECUTIONS',limits_changed=False))))
            await db.commit()

    async def comparison(self):
        await self.init()
        async with aiosqlite.connect(self.path) as db:
            rows=await (await db.execute('SELECT strategy,mode,position_id,payload FROM arb_lifecycle WHERE status="CLOSED" ORDER BY id DESC LIMIT 5000')).fetchall()
        unique={}
        for strategy,mode,pid,payload in rows:
            unique.setdefault((strategy,mode,pid),payload_loads(payload))
        groups={}
        for (strategy,mode,pid),p in unique.items():
            if p.get('net') is None:continue
            g=groups.setdefault((strategy,mode),[]);g.append(p)
        out=['📊 <b>Сравнение · закрытые PAPER-позиции</b>','Только новый журнал. Режимы разделены; контролируемые тесты выполняются в отдельной базе.']
        for (strategy,mode),positions in sorted(groups.items()):
            positions.sort(key=lambda p:p.get('closed_at',p.get('closed',0)))
            current=peak=drawdown=0.;durations=[]
            for p in positions:
                current+=float(p['net']);peak=max(peak,current);drawdown=max(drawdown,peak-current)
                end=p.get('closed_at',p.get('closed'));start=p.get('first_fill_at',p.get('opened_at',p.get('opened')))
                if end is not None and start is not None:durations.append(max(0,end-start))
            average=sum(durations)/len(durations) if durations else 0
            out.append(f'<b>{strategy} · {escape(mode)}</b>\nЗакрыто {len(positions)} · NET ${current:+.4f}\nПросадка закрытых ${drawdown:.4f} · средний цикл {average:.0f} сек.')
        if not groups:out.append('Закрытых сделок новой версии ещё нет. Сравнивать доходность пока рано.')
        return '\n\n'.join(out)

    async def recover_interrupted(self):
        await self.init()
        async with aiosqlite.connect(self.path) as db:
            ids=await (await db.execute('SELECT id FROM arb_candidates WHERE status="CHECKING" AND updated<?',(self.clock()-120,))).fetchall()
            for (ident,) in ids:
                await self.append(db,ident,'QUOTES','FAIL','PREVIOUS_PROCESS_CHECK_INTERRUPTED')
                await db.execute('UPDATE arb_candidates SET status="REJECTED",reason="PREVIOUS_PROCESS_CHECK_INTERRUPTED",updated=? WHERE id=?',(self.clock(),ident))
            await db.commit()

    async def render(self,hours=1):
        s=await self.snapshot(hours)
        names={'SIGNAL':'Кандидаты','MATCH':'Рынки','IDENTITY':'Актив','NETWORK':'Переводная сеть','QUOTES':'Котировки','COSTS':'Экономика','RISK':'Риск','EXECUTOR':'Переданы исполнителю','EXECUTED':'Открыты по модели','CLOSED':'Закрыты'}
        out=[f'🩺 <b>Диагностика · последние {hours:g} ч</b>','PAPER и наблюдение · новая воронка с момента обновления']
        for strategy,label in (('A','Сжатие'),('B','Перевод')):
            counts={stage:n for st,stage,n in s['stages'] if st==strategy}
            out.append('<b>'+label+'</b>\n'+' · '.join(f'{names[k]}: {counts.get(k,0)}' for k in STAGES if k!='NETWORK' or strategy=='B'))
            modes=[f'{mode}: {n}' for st,mode,n in s['counts'] if st==strategy]
            if modes:out[-1]+='\n'+escape(' · '.join(modes))
        if s['reasons']:
            out.append('<b>Причины отказов</b>\n'+'\n'.join(f'{st} · {escape(r[:110])}: {n}' for st,r,n in s['reasons'][:5]))
            fixes=[REMEDIES[r] for _,r,_ in s['reasons'][:3] if r in REMEDIES]
            if fixes:out.append(escape('\n'.join(fixes[:2])))
        if s['unfinished']:out.append(f'Незавершённых проверок старше минуты: {s["unfinished"]}. Подробности в дневнике.')
        if not s['counts']:out.append('Новые проверки ещё не записаны. Исторические счётчики не подменяют эту воронку.')
        return '\n\n'.join(out)
