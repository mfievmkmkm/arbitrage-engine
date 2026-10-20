"""Bounded fresh refusal evidence from the real journal, never test fills."""
import aiosqlite
from html import escape
from .payload_codec import loads

NUMBERS={'observed','threshold','minimum','maximum','unit','gas_usd','native_price_usd',
    'raw_edge_usd','costs_usd','partial_net_usd','immediate_net_usd','reverse_loss_limit_usd',
    'entry_fees_usd','exit_fee_usd','exit_gas_usd','safety_usd','spread_pct','net',
    'net_after_reserve','stress_net','gas','min_spread','max_spread','min_net','stress_drop',
    'buy_age_seconds','sell_age_seconds','maximum_seconds','dex_buy_usd','futures_short_usd'}


def facts(data):
    result={k:v for k,v in data.items() if k in NUMBERS and isinstance(v,(str,int,float,bool))}
    for group in ('economics','thresholds','observed'):
        if isinstance(data.get(group),dict):
            result[group]={k:v for k,v in data[group].items() if k in NUMBERS and isinstance(v,(str,int,float,bool))}
    return result


async def inspect(journal,hours=.25):
    await journal.init();since=journal.clock()-hours*3600
    snapshot=await journal.snapshot(hours)
    examples=[]
    async with aiosqlite.connect(journal.path) as db:
        reasons=[]
        for strategy in ('A','B'):
            rows=await (await db.execute('SELECT strategy,reason,COUNT(*) FROM arb_candidates WHERE strategy=? AND detected>=? AND status="REJECTED" GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 4',(strategy,since))).fetchall()
            reasons.extend(rows)
        for strategy,reason,count in reasons:
            c=await (await db.execute('SELECT id,symbol,buy,sell,updated FROM arb_candidates WHERE strategy=? AND reason=? AND status="REJECTED" AND detected>=? ORDER BY detected DESC LIMIT 1',
                                      (strategy,reason,since))).fetchone()
            if c is None:continue
            checks=await (await db.execute('SELECT payload FROM arb_checks WHERE candidate_id=? AND status="FAIL" ORDER BY id DESC LIMIT 4',(c[0],))).fetchall()
            values={}
            for (payload,) in reversed(checks):values.update(facts(loads(payload)))
            examples.append(dict(strategy=strategy,reason=reason,count=count,id=c[0],symbol=c[1],
                                 buy=c[2],sell=c[3],ts=c[4],facts=values))
    return dict(ts=journal.clock(),window_seconds=hours*3600,counts=snapshot['counts'],
                stages=snapshot['stages'],unfinished=snapshot['unfinished'],examples=examples,
                evidence_mode='CURRENT_MARKET_JOURNAL',limits_changed=False)


def render(report):
    import json
    lines=['🩺 <b>Почему нет входов · 15 минут</b>']
    for strategy in ('A','B'):
        n=sum(row[2] for row in report['counts'] if row[0]==strategy)
        opened=sum(row[2] for row in report['stages'] if tuple(row[:2])==(strategy,'EXECUTED'))
        lines.append(f'<b>{strategy}</b> · проверок {n} · исполнено по модели {opened}')
        for e in [r for r in report['examples'] if r['strategy']==strategy][:3]:
            block=escape(f"{e['symbol']} · {e['reason']} ({e['count']})")
            if e['facts']:block+='\n'+escape(json.dumps(e['facts'],ensure_ascii=False)[:480])
            if len('\n\n'.join(lines))+len(block)<3650:lines.append(block)
    if not report['counts']:lines.append('За этот период проверок ещё нет.')
    lines.append('Причины и числа — из рабочего дневника. Лимиты не изменялись.')
    return '\n\n'.join(lines)
