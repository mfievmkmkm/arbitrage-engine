"""Runtime read-only bridge observation. Never calls enter/hedge/close/finalize."""

import json
from types import SimpleNamespace
import aiosqlite
from .db import Diary
from .dex_live_bridge import Session
from .dex_cex_backend import Backend
from .dex_wallet import Reader
from .live_trade_store import Store


class Observer:
    def __init__(self, session):
        self.session = session
        self.paper = self  # Continue observation even while entry scanning is paused.

    async def observe(self, row):
        proof = await self.session.observe(row["trade_id"])
        verified = proof["status"] == "VERIFIED" and (
            row["phase"] != "DEX_OPEN" or proof.get("mismatch_raw") == 0
        )
        return dict(
            trade_id=row["trade_id"],
            symbol=row["symbol"],
            strategy="cex_dex",
            phase=row["phase"],
            long_venue=row["long_venue"],
            short_venue=row["short_venue"],
            private_verified=verified,
            cash_error=proof.get(
                "reason", "DEX_OPEN_HEDGE_MISMATCH" if not verified else None
            ),
            dex_observation=proof,
            exit_signal="HOLD",
            replay_authorized=False,
        )

    async def cycle(self):
        rows = []
        for row in await self.session.store.active():
            meta = json.loads(row["payload"])
            if meta.get("strategy") != "cex_dex" or not meta.get("dex_live_plan"):
                continue
            result = await self.session.observe(row["trade_id"])
            rows.append(
                dict(
                    strategy="dex_live_observation",
                    symbol=row["symbol"],
                    trade_id=row["trade_id"],
                    phase=row["phase"],
                    status=result["status"],
                    reason=result.get("reason", "READ_ONLY_PRIVATE_OBSERVATION"),
                    proof=result,
                    live_allowed=False,
                    replay_authorized=False,
                )
            )
        return rows


async def build(path, wallet, primary, secondary, clients):
    async def forbidden(*args):
        raise ValueError("DEX_READ_ONLY_OBSERVER")

    store, diary = Store(path), Diary(path)
    await store.init()
    await diary.init()
    backend = Backend(store, diary, clients, lambda v: False, lambda v: False)
    # No signer/key/policy/write transport is reachable from this observer.
    session = Session(
        path,
        SimpleNamespace(journal=wallet),
        Reader(wallet, primary, secondary),
        backend,
        forbidden,
        forbidden,
        forbidden,
        lambda v: False,
        lambda v: False,
    )
    await session.init()
    return Observer(session)
