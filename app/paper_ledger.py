import aiosqlite
from datetime import datetime, timezone


async def restore(path, ledger, risk=None):
    midnight = (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    async with aiosqlite.connect(path) as d:
        async with d.execute(
            "SELECT COALESCE(SUM(current_net_usd),0) FROM paper_positions WHERE status='CLOSED'"
        ) as c:
            ledger.realized = float((await c.fetchone())[0])
        async with d.execute(
            "SELECT COALESCE(SUM(current_net_usd),0) FROM paper_positions WHERE status='CLOSED' AND closed_at>=?",
            (midnight,),
        ) as c:
            today = float((await c.fetchone())[0])
        async with d.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='spot_future_paper'"
        ) as c:
            exists = await c.fetchone()
        if exists:
            async with d.execute(
                "SELECT COALESCE(SUM(net),0) FROM spot_future_paper WHERE status!='OPEN'"
            ) as c:
                ledger.realized += float((await c.fetchone())[0])
            async with d.execute("PRAGMA table_info(spot_future_paper)") as c:
                columns = {x[1] for x in await c.fetchall()}
            if "closed_at" in columns:
                async with d.execute(
                    "SELECT COALESCE(SUM(net),0) FROM spot_future_paper WHERE status!='OPEN' AND closed_at>=?",
                    (midnight,),
                ) as c:
                    today += float((await c.fetchone())[0])
        async with d.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='spot_spot_paper'"
        ) as c:
            exists = await c.fetchone()
        if exists:
            async with d.execute(
                "SELECT COALESCE(SUM(net),0) FROM spot_spot_paper WHERE status!='OPEN'"
            ) as c:
                ledger.realized += float((await c.fetchone())[0])
            async with d.execute(
                "SELECT COALESCE(SUM(net),0) FROM spot_spot_paper WHERE status!='OPEN' AND closed_at>=?",
                (midnight,),
            ) as c:
                today += float((await c.fetchone())[0])
    if risk is not None:
        risk.on_paper_close(today)
    return ledger
