"""Explicit migration of the Railway experiment; never changes LIVE permission."""
import json
import time
from pathlib import Path

TRUTHY = ('true', '1', 'yes', 'on')


def configure(values):
    values.setdefault('PAIRED_PAPER_ENABLED', 'true')
    if str(values['PAIRED_PAPER_ENABLED']).lower() not in TRUTHY:
        return
    if any(str(values.get(k, 'false')).lower() in TRUTHY for k in ('LIVE_ENABLED', 'LIVE_ENTRY_ENABLED', 'DEX_LIVE_ENABLED')):
        raise ValueError('PAIRED_EXPERIMENT_REQUIRES_PAPER_ONLY')
    # The agreed experiment replaces the obsolete $50 / $5 setup.
    values.update(PAPER_CAPITAL_USD='250', NOTIONAL_USD='100',
                  MAX_PAPER_POSITIONS='1', DEX_DUAL_PAPER_ENABLED='true',
                  DEX_REFERENCE_FOCUS='false', DEX_CHANNEL_SIGNALS_ONLY='false')
    values.setdefault('SCAN_INTERVAL', '15')


def migrate_toggles(path, runtime, store):
    marker = Path(str(path) + '.paired_migration.json')
    first = not marker.exists()
    if first:
        runtime.enabled.update(futures_futures=True, spot_futures=False,
                               spot_spot=False, funding_arb=False, dex_paired=True)
    # Deprecated one-sided modes remain monitoring-only across restarts/toggles.
    runtime.enabled.update(dex_channel=False, dex_bybit=False, cex_dex=False)
    runtime.enabled.setdefault('dex_paired', True)
    store.save(runtime.enabled)
    if first:
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(version=1, ts=time.time(),
            mechanism='TWO_LEG_CONVERGENCE', mode='VIRTUAL_ONLY',
            virtual_capital=250, allocation_is_not_profit=True,
            old_history_preserved=True)), encoding='utf-8')
        temporary.replace(marker)


async def futures_cooldown(path, symbol, now=None):
    import aiosqlite
    now = time.time() if now is None else now
    midnight = int(now//86400)*86400
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT current_net_usd,closed_at FROM paper_positions WHERE symbol=? AND status='CLOSED' AND paired_policy IS NOT NULL AND closed_at>=? ORDER BY closed_at DESC", (symbol, midnight)) as c:
            rows = await c.fetchall()
    if sum(net < 0 for net, _ in rows) >= 2:
        return 'PAIRED_ASSET_LOSS_LIMIT'
    if rows and now - rows[0][1] < (3600 if rows[0][0] < 0 else 600):
        return 'PAIRED_ASSET_COOLDOWN'
    return None
