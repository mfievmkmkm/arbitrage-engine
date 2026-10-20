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
