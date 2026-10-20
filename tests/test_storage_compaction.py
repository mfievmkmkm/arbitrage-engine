import gzip
import os
import json
import shutil
import sqlite3
from pathlib import Path
import pytest
from app.payload_codec import pack,unpack,loads
from app.storage_compaction import recover_or_compact,logical_digest,sha
from app.storage_status import inspect


class Remote:
    def __init__(self,directory):self.directory=directory;self.calls=0
    def upload(self,path):
        self.calls+=1;self.saved=self.directory/'remote.gz';shutil.copyfile(path,self.saved)
        return {'backup_files':[{'file_id':'controlled-backup'}]}
    def download(self,metadata,path):shutil.copyfile(self.saved,path)


def seed(path):
    payload=json.dumps({'book':[[1.1,100]]*250,'reason':'CONTROLLED_TEST_DATA'})
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE inventory_paper_events(id INTEGER PRIMARY KEY,ts REAL,reason TEXT,payload TEXT);CREATE TABLE arb_checks(id INTEGER PRIMARY KEY,candidate_id TEXT,payload TEXT);CREATE TABLE transfer_paper_state(id INTEGER PRIMARY KEY,payload TEXT);CREATE TABLE ledger(id INTEGER PRIMARY KEY,payload TEXT);')
        db.executemany('INSERT INTO inventory_paper_events VALUES(?,?,?,?)',[(i,100+i,'TEST',payload) for i in range(1,101)])
        db.execute('INSERT INTO arb_checks VALUES(1,?,?)',('candidate',payload))
        db.execute('INSERT INTO transfer_paper_state VALUES(1,?)',(json.dumps({'position':{'remaining':10,'status':'BASE_IN_TRANSIT'},'balances':{'bybit':150},'realized':-1.5}),))
        db.execute('INSERT INTO ledger VALUES(1,?)',('financial-proof',))


def test_codec_roundtrip_and_checksum():
    raw=json.dumps({'repeat':['test']*1000})
    encoded=pack(raw)
    assert len(encoded)<len(raw)/2 and unpack(encoded)==raw and loads(encoded)==json.loads(raw)
    assert pack(encoded)==encoded and unpack('normal')=='normal'
    broken=json.loads(encoded);broken['sha256']='invalid'
    with pytest.raises(ValueError,match='CHECKSUM'):unpack(json.dumps(broken,separators=(',',':')))


@pytest.mark.skipif(os.name!='posix',reason='Railway offline compaction requires POSIX fsync and single-instance lock')
def test_lossless_compaction_keeps_every_row_and_position_and_local_backup(tmp_path):
    volume=tmp_path/'volume';volume.mkdir();path=volume/'arbitrage.sqlite3';seed(path)
    expected=logical_digest(path);size=path.stat().st_size;remote=Remote(tmp_path)
    result=recover_or_compact(path,remote,force=True)
    assert result['action']=='LOSSLESS_COMPACTION' and result['after_bytes']<size/2
    assert result['all_logical_rows_verified'] and logical_digest(path)==expected and remote.calls==1
    backup=next(volume.glob('storage-backup*.gz'))
    with gzip.open(backup,'rb') as source:restored=tmp_path/'restored.sqlite';restored.write_bytes(source.read())
    assert logical_digest(restored)==expected
    with sqlite3.connect(path) as db:
        assert json.loads(db.execute('SELECT payload FROM transfer_paper_state').fetchone()[0])['position']['remaining']==10
        assert db.execute('SELECT payload FROM ledger').fetchone()[0]=='financial-proof'
        assert '_codec' in db.execute('SELECT payload FROM inventory_paper_events LIMIT 1').fetchone()[0]
    assert not (volume/'storage-recovery.json').exists()


def test_failed_remote_backup_never_mutates_original(tmp_path):
    path=tmp_path/'arbitrage.sqlite3';seed(path);before=path.read_bytes()
    class Failure:
        def upload(self,p):raise RuntimeError('controlled-backup-outage')
    with pytest.raises(RuntimeError):recover_or_compact(path,Failure(),force=True)
    assert path.read_bytes()==before and not (tmp_path/'storage-recovery.json').exists()


@pytest.mark.skipif(os.name!='posix',reason='Railway offline compaction requires POSIX fsync and single-instance lock')
def test_interrupted_install_recovers_from_confirmed_remote_backup(tmp_path,monkeypatch):
    import app.storage_compaction as module
    volume=tmp_path/'volume';volume.mkdir();path=volume/'arbitrage.sqlite3';seed(path)
    expected=logical_digest(path);remote=Remote(tmp_path);copy=module.shutil.copyfile
    def fail_copy(source,dest,*args,**kwargs):
        if Path(dest).name.startswith('storage-backup-') and str(dest).endswith('.tmp'):raise OSError('controlled-power-loss-after-unlink')
        return copy(source,dest,*args,**kwargs)
    monkeypatch.setattr(module.shutil,'copyfile',fail_copy)
    with pytest.raises(OSError):recover_or_compact(path,remote,force=True)
    assert not path.exists() and (volume/'storage-recovery.json').exists()
    monkeypatch.setattr(module.shutil,'copyfile',copy)
    result=recover_or_compact(path,remote,force=True)
    assert result['all_logical_rows_verified'] and logical_digest(path)==expected and remote.calls==1


def test_storage_inspection_does_not_expose_rows_or_mutate(tmp_path):
    path=tmp_path/'arbitrage.sqlite3';seed(path);before=path.read_bytes()
    result=inspect(tmp_path)
    assert 'financial-proof' not in str(result) and path.read_bytes()==before


@pytest.mark.skipif(os.name!='posix',reason='Railway offline compaction requires POSIX fsync and single-instance lock')
def test_corrupt_remote_recovery_stays_blocked(tmp_path,monkeypatch):
    import app.storage_compaction as module
    volume=tmp_path/'volume';volume.mkdir();path=volume/'arbitrage.sqlite3';seed(path);remote=Remote(tmp_path)
    copy=module.shutil.copyfile
    def fail_copy(source,dest,*args,**kwargs):
        if Path(dest).name.startswith('storage-backup-') and str(dest).endswith('.tmp'):raise OSError('controlled-power-loss')
        return copy(source,dest,*args,**kwargs)
    monkeypatch.setattr(module.shutil,'copyfile',fail_copy)
    with pytest.raises(OSError):recover_or_compact(path,remote,force=True)
    monkeypatch.setattr(module.shutil,'copyfile',copy);remote.saved.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='CORRUPT'):recover_or_compact(path,remote,force=True)
    assert not path.exists() and (volume/'storage-recovery.json').exists()


def test_backup_transport_exception_never_exposes_bot_token():
    from app.storage_compaction import TelegramBackup
    remote=TelegramBackup('123456:controlled-secret',42)
    with pytest.raises(RuntimeError) as exc:remote.request('not-a-valid-url/123456:controlled-secret')
    assert 'controlled-secret' not in str(exc.value)


def test_export_unpacks_audit_payload_before_redacting_secrets(tmp_path):
    import csv,io,zipfile
    from app.compact_audit_export import build_sync
    path=tmp_path/'archive.sqlite';raw=json.dumps({'api_key':'DO_NOT_EXPORT','data':['repeated']*500})
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE inventory_paper_events(id INTEGER PRIMARY KEY,ts REAL,reason TEXT,payload TEXT)')
        db.execute('INSERT INTO inventory_paper_events VALUES(1,1000,?,?)',('TEST',pack(raw)))
    output=build_sync(path,tmp_path/'exports')
    with zipfile.ZipFile(output) as z:
        rows=list(csv.DictReader(io.StringIO(z.read('inventory_paper_events.csv').decode())))
        data=json.loads(rows[0]['payload'])
    assert data['api_key']=='***' and data['data']==['repeated']*500 and '_codec' not in data


def test_insufficient_replacement_space_preserves_source_and_does_not_upload(tmp_path,monkeypatch):
    import app.storage_compaction as module
    from types import SimpleNamespace
    path=tmp_path/'arbitrage.sqlite3';seed(path);before=path.read_bytes();remote=Remote(tmp_path)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:SimpleNamespace(free=0,total=path.stat().st_size))
    with pytest.raises(ValueError,match='NOT_ENOUGH_SPACE'):recover_or_compact(path,remote,force=True)
    assert path.read_bytes()==before and remote.calls==0
