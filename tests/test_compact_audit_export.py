import asyncio
import csv
import io
import json
import sqlite3
import zipfile
import pytest
from app.compact_audit_export import build, build_sync


def test_streaming_latest_rows_redaction_manifest_and_database_unchanged(tmp_path):
    path = tmp_path / "audit.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE cex_dex_virtual_paper_channel(id INTEGER PRIMARY KEY,payload TEXT,net REAL)")
        db.executemany("INSERT INTO cex_dex_virtual_paper_channel VALUES(?,?,?)",
                       [(i, json.dumps(dict(symbol="X/USDT", api_key="do-not-export", net=-.5)), -.5) for i in range(1, 61)])
        db.execute("CREATE TABLE cex_dex_virtual_paper_channel_state(id INTEGER,payload TEXT)")
        db.execute("INSERT INTO cex_dex_virtual_paper_channel_state VALUES(1,?)", (json.dumps(dict(realized_net=-30)),))
        db.execute("CREATE TABLE observations(id INTEGER,payload TEXT)")
        db.execute("INSERT INTO observations VALUES(1,'heavy unused observation')")
    before = path.read_bytes()
    archive = asyncio.run(build(path, tmp_path / "out", row_limit=50))
    assert path.read_bytes() == before
    with zipfile.ZipFile(archive) as z:
        assert "observations.csv" not in z.namelist()
        raw = z.read("dex_channel_trades.csv").decode()
        assert "do-not-export" not in raw
        rows = list(csv.DictReader(io.StringIO(raw)))
        assert len(rows) == 50 and rows[0]["id"] == "60" and rows[-1]["id"] == "11"
        assert json.loads(rows[0]["payload"])["api_key"] == "***"
        assert sum(float(r["net"]) for r in rows) == -25
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["tables"]["DEX channel trades"]["truncated"] is True
        assert manifest["tables"]["DEX channel trades"]["total_rows"] == 60


def test_bad_payload_omitted_and_empty_table_has_header(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE cex_dex_virtual_paper_bybit(id INTEGER,payload TEXT)")
        db.execute("INSERT INTO cex_dex_virtual_paper_bybit VALUES(1,'password=unsafe')")
        db.execute("CREATE TABLE cex_dex_virtual_paper_bybit_marks(id INTEGER,net REAL)")
    with zipfile.ZipFile(build_sync(path,tmp_path / "out")) as z:
        assert "unsafe" not in z.read("dex_bybit_trades.csv").decode()
        assert z.read("dex_bybit_marks.csv").decode().strip() == "id,net"
    with pytest.raises(ValueError):build_sync(path,tmp_path,row_limit=100000)


def test_current_scanner_evidence_is_exported_and_latest_snapshot_replaces(tmp_path):
    from app.scan_audit import save
    async def go():
        path=tmp_path/'db'
        await save(path,'paired',dict(checked=1,reason='old'))
        await save(path,'paired',dict(checked=12,reason='KYBER_FILTERED_LIQUIDITY'))
        await save(path,'transfer',dict(checked=3,spread_pct=-.5))
        archive=await build(path,tmp_path/'out')
        with zipfile.ZipFile(archive) as z:
            rows=list(csv.DictReader(io.StringIO(z.read('dex_scanner_state.csv').decode())))
            scopes={r['scope']:json.loads(r['payload']) for r in rows}
            assert len(rows)==2 and scopes['paired']['checked']==12
            assert scopes['transfer']['spread_pct']==-.5
    asyncio.run(go())
