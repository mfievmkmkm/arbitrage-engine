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
    # A previous migration persisted both agreed A routes as disabled. The
    # operator requested PAPER auto-entry, but reboot retained the obsolete
    # off toggles. Re-activate these two PAPER routes exactly once on upgrade;
    # future Telegram off toggles remain authoritative across restarts.
    # This is NOT LIVE authorization and bypasses no per-candidate risk gate.
    convergence_marker = Path(str(path) + '.convergence_v2.json')
    activation = not convergence_marker.exists()
    if activation:
        runtime.enabled.update(futures_futures=True, spot_futures=True)
    # Deprecated one-sided modes remain monitoring-only across restarts/toggles.
    runtime.enabled.update(dex_channel=False, dex_bybit=False, cex_dex=False)
    runtime.enabled.setdefault('dex_paired', True)
    store.save(runtime.enabled)
    if activation:
        temporary = convergence_marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(version=2, ts=time.time(),
            mode='PAPER_ONLY', activated=['futures_futures','spot_futures'],
            one_time=True, live_authority=False, old_history_preserved=True)),
            encoding='utf-8')
        temporary.replace(convergence_marker)
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


async def require_monitoring(path, enabled):
    """An env change cannot abandon open paired positions on redeploy."""
    if enabled:
        return
    import aiosqlite
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions'") as c:
            if await c.fetchone():
                async with db.execute('PRAGMA table_info(paper_positions)') as columns:
                    has_policy='paired_policy' in {r[1] for r in await columns.fetchall()}
                if has_policy:
                    async with db.execute("SELECT COUNT(*) FROM paper_positions WHERE status!='CLOSED' AND paired_policy IS NOT NULL") as count:
                        if (await count.fetchone())[0]:
                            raise ValueError('OPEN_PAIRED_FUTURES_REQUIRES_PAIRED_PAPER_ENABLED')
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cex_dex_virtual_paper_paired'") as c:
            if not await c.fetchone():
                return
        async with db.execute("SELECT COUNT(*) FROM cex_dex_virtual_paper_paired WHERE status!='CLOSED'") as c:
            if (await c.fetchone())[0]:
                raise ValueError('OPEN_PAIRED_DEX_REQUIRES_PAIRED_PAPER_ENABLED')


def load_pause(path):
    target=Path(str(path)+'.paper_controls.json')
    if not target.exists():return False
    data=json.loads(target.read_text(encoding='utf-8'))
    if type(data.get('paused')) is not bool:
        raise ValueError('PAPER_PAUSE_STATE_INVALID')
    return data['paused']


def save_pause(path, paused):
    if type(paused) is not bool:raise ValueError('PAPER_PAUSE_STATE_INVALID')
    target=Path(str(path)+'.paper_controls.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix('.tmp')
    tmp.write_text(json.dumps(dict(paused=paused)),encoding='utf-8')
    tmp.replace(target)
