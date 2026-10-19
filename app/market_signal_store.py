"""Independent durable notification queue; contains no execution authority."""
import json
import time
import aiosqlite


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS market_signal_queue(
                    key TEXT PRIMARY KEY, kind TEXT, ts REAL, payload TEXT,
                    delivered INTEGER DEFAULT 0, result TEXT);
                CREATE TABLE IF NOT EXISTS market_signal_settings(
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER);
                INSERT OR IGNORE INTO market_signal_settings VALUES(1,1);
                CREATE TABLE IF NOT EXISTS signal_transfer_control(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);
                INSERT OR IGNORE INTO signal_transfer_control VALUES(1,'{"mode":"manual","budget":25,"min_spread":5,"max_spread":10,"min_net":0.25,"stress_drop":3}');
                CREATE TABLE IF NOT EXISTS signal_transfer_tickets(token TEXT PRIMARY KEY,expires REAL,symbol TEXT,buy TEXT,sell TEXT);
            ''')
            await db.commit()

    async def enabled(self):
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT enabled FROM market_signal_settings WHERE id=1')).fetchone()
        return bool(row[0])

    async def toggle(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE market_signal_settings SET enabled=1-enabled WHERE id=1')
            await db.commit()

    async def enqueue(self, key, kind, payload):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT OR IGNORE INTO market_signal_queue(key,kind,ts,payload) VALUES(?,?,?,?)',
                             (key, kind, self.clock(), json.dumps(payload, allow_nan=False)))
            await db.commit()

    async def pending(self):
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute('SELECT key,kind,ts,payload,result FROM market_signal_queue WHERE delivered=0 ORDER BY ts,key LIMIT 10')).fetchall()
        return [dict(key=k, kind=t, ts=ts, payload=json.loads(p), result=r) for k,t,ts,p,r in rows]

    async def result(self, key, text):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE market_signal_queue SET result=? WHERE key=?', (text, key))
            await db.commit()

    async def delivered(self, key):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE market_signal_queue SET delivered=1 WHERE key=?', (key,))
            await db.commit()

    async def control(self):
        async with aiosqlite.connect(self.path) as db:
            row=await (await db.execute('SELECT payload FROM signal_transfer_control WHERE id=1')).fetchone()
        return {'daily_loss':1.25,'total_loss':2.5,'revision':0,**json.loads(row[0])}

    async def set_control(self,key,value):
        from .strategy_math import D
        if key not in ('mode','budget','min_spread','max_spread','min_net','stress_drop','dex_deposit_fee_pct','mexc_deposit_fee_pct','daily_loss','total_loss'):
            raise ValueError('Неизвестная настройка')
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            p=json.loads((await (await db.execute('SELECT payload FROM signal_transfer_control WHERE id=1')).fetchone())[0])
            before=dict(p)
            if key=='mode':
                if value not in ('observe','manual','auto'):raise ValueError('Режим должен быть observe/manual/auto')
                p[key]=value
            else:
                v=D(value)
                if not (('budget'==key and 5<=v<=100) or ('min_spread'==key and 2<=v<=20) or
                        ('max_spread'==key and 2<=v<=30) or ('min_net'==key and D('.25')<=v<=5) or
                        ('stress_drop'==key and 1<=v<=10) or ('daily_loss'==key and D('.1')<=v<=D('1.25')) or ('total_loss'==key and D('.1')<=v<=D('2.5')) or (key in ('dex_deposit_fee_pct','mexc_deposit_fee_pct') and 0<=v<=1)):
                    raise ValueError({'daily_loss':'Дневной стоп: от 0,1 до 1,25 USD','total_loss':'Стоп теста: от 0,1 до 2,5 USD','budget':'Покупка: от 5 до 100 USD','min_spread':'Спред от: от 2 до 20%','max_spread':'Спред до: от 2 до 30%','min_net':'NET: от 0,25 до 5 USD','stress_drop':'Стресс: от 1 до 10%','dex_deposit_fee_pct':'Комиссия ввода: от 0 до 1%','mexc_deposit_fee_pct':'Комиссия ввода MEXC: от 0 до 1%'}[key])
                p[key]=float(v)
                if key in ('dex_deposit_fee_pct','mexc_deposit_fee_pct'):p[key.replace('_pct','_set_at')]=self.clock()
            if p['min_spread']>p['max_spread']:raise ValueError('Минимальный спред больше максимума')
            p['revision']=before.get('revision',0)+1
            await db.execute('UPDATE signal_transfer_control SET payload=? WHERE id=1',(json.dumps(p),))
            await db.execute('CREATE TABLE IF NOT EXISTS arb_config_events(id INTEGER PRIMARY KEY,ts REAL NOT NULL,strategy TEXT NOT NULL,before_payload TEXT NOT NULL,after_payload TEXT NOT NULL)')
            await db.execute('INSERT INTO arb_config_events(ts,strategy,before_payload,after_payload) VALUES(?,?,?,?)',
                             (self.clock(),'B',json.dumps(before),json.dumps(p)))
            await db.commit()
        return p

    async def issue(self,signal):
        import secrets
        token=secrets.token_urlsafe(12)
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT INTO signal_transfer_tickets VALUES(?,?,?,?,?)',
                             (token,self.clock()+30,signal['symbol'],signal['buy'],signal['sell']))
            await db.commit()
        return token

    async def claim(self,token):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('BEGIN IMMEDIATE')
            row=await (await db.execute('SELECT expires,symbol,buy,sell FROM signal_transfer_tickets WHERE token=?',(token,))).fetchone()
            await db.execute('DELETE FROM signal_transfer_tickets WHERE token=?',(token,))
            await db.commit()
        if row is None or row[0]<self.clock():raise ValueError('Кнопка устарела. Открой карточку заново')
        return dict(symbol=row[1],buy=row[2],sell=row[3])

    async def signal(self,ident):
        async with aiosqlite.connect(self.path) as db:
            row=await (await db.execute('SELECT payload,ts FROM market_signal_queue WHERE kind="spot" AND json_extract(payload,"$.id")=? ORDER BY ts DESC LIMIT 1',(ident,))).fetchone()
        if row is None or self.clock()-row[1]>300:raise ValueError('Сигнал отсутствует или устарел')
        return json.loads(row[0])
