"""Read-only sizing and whitelisted PAPER aggregates; no raw payloads or secrets."""
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
                    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    summary={}
                    if 'signal_transfer_control' in tables:
                        row=db.execute('SELECT payload FROM signal_transfer_control WHERE id=1').fetchone()
                        control=json.loads(row[0]) if row else {}
                        summary['B_control']={k:control[k] for k in ('mode','budget','min_spread','max_spread','min_net','dex_deposit_fee_pct','dex_deposit_fee_set_at') if k in control}
                    if 'signal_transfer_state' in tables:
                        row=db.execute('SELECT payload FROM signal_transfer_state WHERE id=1').fetchone()
                        state=json.loads(row[0]) if row else {}
                        summary['B_state']={k:state[k] for k in ('closed','wins','losses','realized') if k in state}
                        summary['B_state']['has_position']=bool(state.get('position'))
                    if 'cex_dex_virtual_paper_paired' in tables:
                        summary['A_positions']=db.execute('SELECT status,COUNT(*) FROM cex_dex_virtual_paper_paired GROUP BY status').fetchall()
                    controls=path.parent/'runtime_state.json.trading_controls.json'
                    if controls.exists():
                        profile=(json.loads(controls.read_text()).get('profiles') or {}).get('paper',{})
                        summary['A_control']={k:profile[k] for k in ('mode','notional_usd','min_net_usd','stop_loss_usd') if k in profile}
                    toggles=path.parent/'runtime_state.json.strategies.json'
                    if toggles.exists():
                        values=json.loads(toggles.read_text())
                        summary['entry_toggles']={k:values[k] for k in ('spot_inventory','dex_paired','product_dex_paired','market_signals') if type(values.get(k)) is bool}
                    item['paper_summary']=summary

        except (OSError,sqlite3.Error) as exc:item['inspection_error']=type(exc).__name__
        result['files'].append(item)
    return result


def report(directory='/data'):
    try:print('STORAGE_STATUS '+json.dumps(inspect(directory),ensure_ascii=False),flush=True)
    except Exception as exc:print('STORAGE_STATUS_UNAVAILABLE '+type(exc).__name__,flush=True)
