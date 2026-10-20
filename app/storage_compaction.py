"""Offline lossless audit packing with verified remote backup and crash hold.

Run only under the existing single-instance lock, before trading modules start.
No row is age-deleted. A durable Telegram backup must succeed before replacement.
A persisted recovery manifest blocks normal startup after an interrupted install.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.request
from contextlib import closing
from pathlib import Path
from .payload_codec import TABLES,pack,unpack


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def seal(path):
    # Windows _commit (used by os.fsync) requires a writable file descriptor.
    # Keep an actual fsync; do not mask an I/O failure or weaken backup safety.
    with open(path,'r+b') as f:
        os.fsync(f.fileno())


def syncdir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def open_db(path,mode='ro'):
    return sqlite3.connect(Path(path).absolute().as_uri()+'?mode='+mode,uri=True,timeout=10)


def logical_digest(path):
    result={}
    with closing(open_db(path)) as db:
        if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('STORAGE_INTEGRITY_FAILED')
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in names:
            name='"'+table.replace('"','""')+'"'
            columns=[r[1] for r in db.execute('PRAGMA table_info('+name+')')]
            cursor=db.execute('SELECT * FROM '+name+' ORDER BY rowid');offset=0
            h=hashlib.sha256();count=0
            index=columns.index('payload')+offset if table in TABLES and 'payload' in columns else None
            for row in cursor:
                row=list(row)
                if index is not None:row[index]=unpack(row[index])
                data=json.dumps(row,ensure_ascii=False,separators=(',',':'),default=lambda x:{'_bytes':bytes(x).hex()}).encode()
                h.update(len(data).to_bytes(8,'big'));h.update(data);count+=1
            result[table]=[count,h.hexdigest()]
    return result


def build(source,target):
    with closing(open_db(source)) as src,closing(sqlite3.connect(target)) as db:
        src.backup(db)
        db.execute('PRAGMA journal_mode=DELETE')
        available={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in sorted(TABLES&available):
            cursor=db.execute('SELECT rowid,payload FROM '+table)
            while rows:=cursor.fetchmany(250):
                changed=[(packed,rowid) for rowid,raw in rows if (packed:=pack(raw))!=raw]
                if changed:db.executemany('UPDATE '+table+' SET payload=? WHERE rowid=?',changed)
            db.commit()
        db.execute('VACUUM')
    if logical_digest(source)!=logical_digest(target):raise ValueError('STORAGE_LOGICAL_HISTORY_MISMATCH')
    seal(target)


class TelegramBackup:
    def __init__(self,token,admin):
        if not token or not str(admin).isdigit() or int(admin)<=0:raise ValueError('STORAGE_BACKUP_ADMIN_REQUIRED')
        self.base='https://api.telegram.org/bot'+token+'/'
        self.files='https://api.telegram.org/file/bot'+token+'/'
        self.admin=str(admin)

    def request(self,url,data=None,headers=None):
        # Do not propagate urllib errors: their URL contains the bot token.
        try:
            with urllib.request.urlopen(urllib.request.Request(url,data=data,headers=headers or {}),timeout=180) as r:
                return r.read()
        except Exception:raise RuntimeError('STORAGE_BACKUP_TRANSPORT_FAILED') from None

    def upload(self,path):
        files=[]
        with open(path,'rb') as stream:
            while chunk:=stream.read(19*1024*1024):
                boundary='arbbackup'+str(time.time_ns())
                index=len(files)+1
                body=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{self.admin}\r\n'
                      f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n'
                      f'Резервная копия базы перед сжатием · часть {index}. История сохранена.\r\n'
                      f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="arbitrage-backup.sqlite3.gz.part{index:02d}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                raw=self.request(self.base+'sendDocument',body+chunk+f'\r\n--{boundary}--\r\n'.encode(),{'Content-Type':'multipart/form-data; boundary='+boundary})
                data=json.loads(raw)
                if not data.get('ok'):raise ValueError('STORAGE_REMOTE_BACKUP_NOT_CONFIRMED')
                doc=data['result']['document']
                if doc.get('file_size')!=len(chunk):raise ValueError('STORAGE_REMOTE_BACKUP_SIZE_MISMATCH')
                files.append(dict(file_id=doc['file_id'],bytes=len(chunk),sha256=hashlib.sha256(chunk).hexdigest(),message_id=data['result']['message_id']))
        if not files:raise ValueError('STORAGE_EMPTY_BACKUP')
        return dict(backup_files=files)

    def download(self,metadata,path):
        from urllib.parse import urlencode
        with open(path,'wb') as stream:
            for entry in metadata['backup_files']:
                response=json.loads(self.request(self.base+'getFile',urlencode({'file_id':entry['file_id']}).encode()))
                if not response.get('ok'):raise ValueError('STORAGE_REMOTE_RECOVERY_UNAVAILABLE')
                data=self.request(self.files+response['result']['file_path'])
                if len(data)!=entry['bytes'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('STORAGE_REMOTE_RECOVERY_CORRUPT')
                stream.write(data)


def write_manifest(path,data):
    temporary=path.with_suffix('.prepared.tmp')
    temporary.unlink(missing_ok=True)
    try:
        with open(temporary,'x',encoding='utf-8') as f:
            json.dump(data,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
        os.replace(temporary,path);syncdir(path.parent)
    finally:temporary.unlink(missing_ok=True)


def recover_or_compact(path,remote,*,force=False):
    path=Path(path).absolute();marker=path.with_name('storage-recovery.json')
    disk=shutil.disk_usage(path.parent)
    if not marker.exists() and (not path.exists() or (not force and disk.free>max(32*1024*1024,disk.total*.15))):return {'action':'NOT_NEEDED'}
    with tempfile.TemporaryDirectory(prefix='arb-storage-') as td:
        temporary=Path(td);original=temporary/'source.sqlite3';target=temporary/'packed.sqlite3';gz=temporary/'backup.sqlite3.gz'
        metadata=json.loads(marker.read_text()) if marker.exists() else None
        if metadata:
            saved=path.parent/metadata['backup_name']
            if saved.is_file() and sha(saved)==metadata['gzip_sha256']:shutil.copyfile(saved,gz)
            else:remote.download(metadata,gz)
            if sha(gz)!=metadata['gzip_sha256']:raise ValueError('STORAGE_RECOVERY_BACKUP_CORRUPT')
            with gzip.open(gz,'rb') as src,open(original,'wb') as dst:shutil.copyfileobj(src,dst,1024*1024)
            if sha(original)!=metadata['source_sha256']:raise ValueError('STORAGE_RECOVERY_SOURCE_CORRUPT')
        else:
            # SQLite backup includes a committed WAL, if present, without guessing.
            with closing(open_db(path)) as src,closing(sqlite3.connect(original)) as dst:src.backup(dst)
            with open(original,'rb') as src,gzip.open(gz,'wb',compresslevel=6) as dst:shutil.copyfileobj(src,dst,1024*1024)
            with gzip.open(gz,'rb') as f:
                h=hashlib.sha256()
                while chunk:=f.read(1024*1024):h.update(chunk)
            if h.hexdigest()!=sha(original):raise ValueError('STORAGE_BACKUP_VERIFY_FAILED')
        build(original,target)
        if metadata:
            path.with_suffix('.storage.tmp').unlink(missing_ok=True)
            (path.parent/metadata['backup_name']).with_suffix('.tmp').unlink(missing_ok=True)
        # Keep one sealed local full backup as well as the verified Telegram copy.
        if target.stat().st_size+gz.stat().st_size+32*1024*1024>disk.free+(path.stat().st_size if path.exists() else 0):
            raise ValueError('STORAGE_COMPACTION_NOT_ENOUGH_SPACE')
        if target.stat().st_size>=original.stat().st_size*.9 and metadata is None:raise ValueError('STORAGE_COMPACTION_NO_MATERIAL_GAIN')
        if metadata is None:
            metadata=dict(version=1,db_name=path.name,source_sha256=sha(original),gzip_sha256=sha(gz),
                backup_name='storage-backup-'+str(int(time.time()))+'.sqlite3.gz',**remote.upload(gz))
            write_manifest(marker,metadata)
        # The durable manifest and confirmed remote backup precede any removal.
        path.unlink(missing_ok=True)
        for suffix in ('-wal','-shm','-journal'):Path(str(path)+suffix).unlink(missing_ok=True)
        syncdir(path.parent)
        backup=path.parent/metadata['backup_name']
        if not backup.is_file() or sha(backup)!=metadata['gzip_sha256']:
            backup_tmp=backup.with_suffix('.tmp');shutil.copyfile(gz,backup_tmp);os.chmod(backup_tmp,0o600);seal(backup_tmp);os.replace(backup_tmp,backup);syncdir(path.parent)
        target_tmp=path.with_suffix('.storage.tmp');shutil.copyfile(target,target_tmp);os.chmod(target_tmp,0o600);seal(target_tmp)
        if sha(target_tmp)!=sha(target):raise ValueError('STORAGE_INSTALL_CHECKSUM_MISMATCH')
        os.replace(target_tmp,path);syncdir(path.parent)
        after=path.stat().st_size
        marker.unlink();syncdir(path.parent)
        return {'action':'LOSSLESS_COMPACTION','before_bytes':original.stat().st_size,'after_bytes':after,
                'backup_bytes':backup.stat().st_size,'free_bytes':shutil.disk_usage(path.parent).free,'all_logical_rows_verified':True}


def archive_duplicate_backups(path,remote):
    """Move sealed backup copies to the owner with durable retrieval receipts.

    Never touches the working SQLite database or its journals. Acknowledged
    remote copies and a synced receipt must precede removal of a local copy.
    """
    import re
    parent=Path(path).parent;archived=0
    for backup in sorted(parent.glob('storage-backup-*.sqlite3.gz')):
        disk=shutil.disk_usage(parent)
        if disk.free>max(32*1024*1024,disk.total*.15):break
        if backup.is_symlink() or not re.fullmatch(r'storage-backup-[0-9]+\.sqlite3\.gz',backup.name):continue
        digest=sha(backup);receipt=backup.with_name(backup.name+'.remote.json')
        if receipt.exists():
            proof=json.loads(receipt.read_text())
            if proof.get('gzip_sha256')!=digest or proof.get('bytes')!=backup.stat().st_size:
                raise ValueError('STORAGE_BACKUP_RECEIPT_CONFLICT')
        else:
            # Read to EOF to verify gzip CRC before moving this backup copy.
            with gzip.open(backup,'rb') as f:
                while f.read(1024*1024):pass
            proof=dict(version=1,backup_name=backup.name,gzip_sha256=digest,bytes=backup.stat().st_size,**remote.upload(backup))
            write_manifest(receipt,proof)
        if sha(backup)!=digest:raise ValueError('STORAGE_BACKUP_CHANGED_DURING_ARCHIVE')
        backup.unlink();syncdir(parent);archived+=1
    return archived


def startup(path):
    if not os.getenv('RAILWAY_ENVIRONMENT_ID'):return
    path=Path(path);disk=shutil.disk_usage(path.parent)
    marker=path.with_name('storage-recovery.json')
    if not marker.exists() and disk.free>max(32*1024*1024,disk.total*.15):return
    recovering=marker.exists()
    print('STORAGE_COMPACTION_BEGIN mode=PAPER history=LOSSLESS',flush=True)
    try:
        remote=TelegramBackup(os.getenv('BOT_TOKEN'),os.getenv('ADMIN_ID'))
        if not recovering:
            count=archive_duplicate_backups(path,remote)
            if count:print('STORAGE_BACKUP_ARCHIVED '+json.dumps(dict(copies=count,free_bytes=shutil.disk_usage(path.parent).free)),flush=True)
            disk=shutil.disk_usage(path.parent)
            if disk.free>max(32*1024*1024,disk.total*.15):return
        print('STORAGE_COMPACTION '+json.dumps(recover_or_compact(path,remote)),flush=True)
    except Exception as exc:
        # Optional maintenance cannot masquerade as an unfinished DB install.
        if not recovering and not marker.exists() and path.is_file() and shutil.disk_usage(path.parent).free>=16*1024*1024:
            with closing(open_db(path,'rw')) as db:
                if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise RuntimeError('STORAGE_RECOVERY_REQUIRED') from None
            print('STORAGE_MAINTENANCE_DEFERRED '+type(exc).__name__+' '+str(exc)[:120],flush=True)
            return
        print('STORAGE_COMPACTION_HOLD '+type(exc).__name__+' '+str(exc)[:120],flush=True)
        raise RuntimeError('STORAGE_RECOVERY_REQUIRED') from None


def bootstrap(directory='/data'):
    """Before launcher path discovery, including a missing DB after interrupted install."""
    if not os.getenv('RAILWAY_ENVIRONMENT_ID'):return
    if any(os.getenv(k,'false').lower() in ('1','true','yes','on') for k in ('LIVE_ENABLED','LIVE_ENTRY_ENABLED','DEX_LIVE_ENABLED')):
        raise RuntimeError('STORAGE_MAINTENANCE_PAPER_ONLY')
    root=Path(directory);paths=set()
    for marker in root.rglob('storage-recovery.json'):
        name=json.loads(marker.read_text())['db_name']
        if Path(name).name!=name:raise ValueError('STORAGE_RECOVERY_PATH_INVALID')
        paths.add(marker.parent/name)
    for path in root.rglob('*.sqlite3'):
        if path.is_symlink():continue
        with path.open('rb') as f:
            if f.read(16)==b'SQLite format 3\x00':paths.add(path)
    import fcntl
    for path in sorted(paths):
        with (path.parent/'paper.instance.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            startup(path)
