"""Read-only disk/table sizing. Never prints row contents or secret values."""
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def inspect(directory):
    root=Path(directory)
    if not root.is_dir():return {'present':False}
    disk=shutil.disk_usage(root)
    files=sorted((p for p in root.rglob('*') if p.is_file() and not p.is_symlink()),key=lambda p:p.stat().st_size,reverse=True)[:15]
    result={'free_bytes':disk.free,'total_bytes':disk.total,'files':[]}
    for path in files:
        item={'name':str(path.relative_to(root)),'bytes':path.stat().st_size}
        try:
            with path.open('rb') as stream:header=stream.read(16)
            if header==b'SQLite format 3\x00':
                with closing(sqlite3.connect(path.absolute().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                    db.execute('PRAGMA query_only=ON')
                    item['free_pages']=db.execute('PRAGMA freelist_count').fetchone()[0]
                    item['page_size']=db.execute('PRAGMA page_size').fetchone()[0]
                    item['tables']=db.execute('SELECT name,SUM(pgsize) AS size FROM dbstat GROUP BY name ORDER BY size DESC LIMIT 12').fetchall()
        except (OSError,sqlite3.Error) as exc:item['inspection_error']=type(exc).__name__
        result['files'].append(item)
    return result


def report(directory='/data'):
    try:print('STORAGE_STATUS '+json.dumps(inspect(directory),ensure_ascii=False),flush=True)
    except Exception as exc:print('STORAGE_STATUS_UNAVAILABLE '+type(exc).__name__,flush=True)
