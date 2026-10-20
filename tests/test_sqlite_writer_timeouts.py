"""SQLITE busy timeout allows concurrent paper modules time to commit.

A longer timeout does not create any order authority or change transactions.
"""
import asyncio
import sqlite3

from app.db import Diary


def test_paper_diary_waits_for_busy_database_instead_of_default_five_seconds(tmp_path, monkeypatch):
    import app.db as dbmod
    observed = []
    real = dbmod.aiosqlite.connect
    def capture(path, *a, **kw):
        observed.append(kw.get('timeout'))
        return real(path, *a, **kw)
    monkeypatch.setattr(dbmod.aiosqlite, 'connect', capture)
    async def run():
        journal=Diary(str(tmp_path / 'paper.sqlite3'))
        await journal.init()
        await journal.record([dict(ts=1,symbol='TEST/USDT:USDT',buy='bybit',sell='okx',
             raw=.2,executable=.1,fee_pct=.05,hypothetical_edge=.01,notional=100)])
    asyncio.run(run())
    assert observed and all(x == 30 for x in observed)
    with sqlite3.connect(tmp_path / 'paper.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 1


def test_journal_and_audit_use_bounded_busy_timeouts(tmp_path, monkeypatch):
    import app.decision_journal as j
    import app.scan_audit as audit
    real = j.aiosqlite.connect
    observed=[]
    def capture(path, *a, **kw):
        observed.append(kw.get('timeout'))
        return real(path, *a, **kw)
    monkeypatch.setattr(j.aiosqlite, 'connect', capture)
    async def run():
        journal=j.Journal(str(tmp_path/'audit.sqlite3'))
        await journal.init()
        await audit.save(str(tmp_path/'audit.sqlite3'),'scope',dict(ok=True))
    asyncio.run(run())
    assert 20 in observed and 15 in observed


def test_confirmed_paired_paper_close_reports_reason_and_model_pnl(caplog):
    from app.paper import PaperPosition, PaperEngine
    class DiaryStub:
        def __init__(self): self.saved=[]
        async def close_paper_position(self, value, reason):
            self.saved.append((value, reason))
    diary=DiaryStub()
    paper=PaperEngine(diary, capital=250)
    p=PaperPosition(id=5,symbol='TEST/USDT:USDT',buy='bybit',sell='okx',
                    notional=100,entry_buy=100,entry_sell=102,entry_spread=2,opened_at=1,
                    paired_policy={'mechanism':'TWO_LEG_CONVERGENCE'}, current_net_usd=-.47)
    paper.positions[p.id]=p
    async def run():
        with caplog.at_level('INFO',logger='arbitrage'):
            return await paper.close(p.id,'TIME_STOP')
    result=asyncio.run(run())
    assert diary.saved and diary.saved[0][1]=='TIME_STOP'
    assert result.status=='CLOSED' and not paper.positions
    assert 'PAPER_FF_CLOSED id=5' in caplog.text
    assert 'model_net_usd=-0.470000' in caplog.text
