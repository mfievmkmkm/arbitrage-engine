"""Latest bounded scanner evidence, separate from financial trade state."""
import json
import aiosqlite


async def save(path,scope,payload):
    async with aiosqlite.connect(path, timeout=15.0) as db:
        await db.execute('CREATE TABLE IF NOT EXISTS dex_scanner_audit(scope TEXT PRIMARY KEY,payload TEXT)')
        await db.execute('INSERT INTO dex_scanner_audit VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload',
                         (scope,json.dumps(payload,allow_nan=False)))
        await db.commit()
