import asyncio
from types import SimpleNamespace as NS
from app.inventory_public_source import Service


def test_disabled_legacy_inventory_monitors_without_capturing_more_snapshots():
    async def run():
        called=[]
        class Source:
            async def capture(self):raise AssertionError('disabled capture must not run')
        class Paper:
            positions={'existing':{'residual':'0','status':'BASE_IN_TRANSIT'}}
            async def monitor(self,source):called.append('monitor')
        s=Service(Source(),Paper());s.entry_enabled=False
        assert await s.cycle()==[] and called==['monitor']
    asyncio.run(run())


def test_identity_replay_does_not_compete_with_database_writer(tmp_path):
    import sqlite3
    from app.asset_registry import Registry
    async def run():
        path=tmp_path/'identity.sqlite';registry=Registry(path,lambda:100)
        route=dict(cex_venue='bybit',cex_symbol='X/USDT:USDT',base='X',chain_id=56,asset='0x'+'1'*40)
        evidence=dict(verified_at=100,expires_at=400,source='CONTROLLED_TEST')
        market=dict(base='X',contract=True,contractSize=1)
        expected=await registry.record(route,evidence,market)
        with sqlite3.connect(path,isolation_level=None) as lock:
            lock.execute('BEGIN EXCLUSIVE')
            replay=await asyncio.wait_for(registry.record(route,evidence,market),.5)
            assert replay==expected
            lock.rollback()
        expired=Registry(path,lambda:401)
        try:await expired.record(route,evidence,market)
        except ValueError as exc:assert 'EXPIRED' in str(exc)
        else:raise AssertionError('Expired evidence must never bypass verification')
    asyncio.run(run())


def test_paper_server_enables_wal_before_runtime(tmp_path,monkeypatch):
    import sqlite3,os,pytest
    if os.name!='posix':pytest.skip('Railway single-instance startup uses POSIX flock')
    from app import paper_server
    path=tmp_path/'paper.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE ledger(id INTEGER PRIMARY KEY,net REAL)');db.execute('INSERT INTO ledger VALUES(1,-1.25)')
    called=[]
    def main(*a,**kw):
        with sqlite3.connect(path) as db:
            assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
            assert db.execute('SELECT net FROM ledger').fetchone()[0]==-1.25
        called.append('runtime')
    monkeypatch.setattr(paper_server.runpy,'run_module',main)
    paper_server.run_database(path)
    assert called==['runtime']
