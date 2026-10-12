import asyncio
import pytest
from app.dex_signal_focus import Signals, quality, focus_toggles

MINT = "MukLDtJ8Cx9DxLbeyLRSWPSposTMWuwHANbuaudpump"
TEXT = f"🔴 SHORT? #OTC [DEX_DRIVEN] Spread -6.97% detected\n#SOLANA Dep 🟢\n{MINT}"
ROUTE = dict(chain_id="solana", asset=MINT, base="OTC", cex_venue="mexc", mode="reference")


def test_signal_scope_age_dedup_and_restart_cooldown(tmp_path):
    now = [100.]
    path = tmp_path / "signals.json"
    s = Signals(path, lambda: now[0])
    with pytest.raises(ValueError):
        s.add(TEXT, [dict(ROUTE, asset=MINT.lower())], 99, "channel")
    s.add(TEXT, [ROUTE], 99, "channel")
    assert s.get(ROUTE)[0]["side"] == "sell"
    with pytest.raises(ValueError):
        s.add(TEXT, [ROUTE], 99, "channel")
    with pytest.raises(ValueError):
        s.add(TEXT + "\nAligned in 31 sec", [ROUTE], 100, "channel")
    now[0] = 191
    assert s.get(ROUTE)[1] == "DEX_CHANNEL_SIGNAL_REQUIRED"
    with pytest.raises(ValueError):
        s.add(TEXT, [ROUTE], 99, "channel")
    s.closed(dict(chain_id="solana", virtual_route_key=["solana", "stable", MINT], cex_venue="mexc", net=-.8))
    restored = Signals(path, lambda: now[0])
    assert restored.get(ROUTE)[1] == "DEX_SIGNAL_LOSS_COOLDOWN"
    now[0] = 792
    assert restored.get(ROUTE)[1] == "DEX_CHANNEL_SIGNAL_REQUIRED"


def test_reward_gate_rejects_audit_otc_and_wide_exit_book():
    book = dict(bids=[[.006515, 700]], asks=[[.006549, 213]])
    assert quality(19.74045, .0529707898, -.144, book) == "DEX_REFERENCE_REWARD_TOO_SMALL"
    assert quality(20, 1, -.1, dict(bids=[[.006465, 1]], asks=[[.00677, 1]])) == "DEX_REFERENCE_BOOK_TOO_WIDE"
    assert quality(20, 1, -.2, book) == "DEX_REFERENCE_ROUNDTRIP_TOO_COSTLY"
    assert quality(20, .5, -.1, book) is None
    assert focus_toggles(dict(spot_futures=True, cex_dex=False, funding_arb=True)) == dict(spot_futures=False, cex_dex=True, funding_arb=False)


def test_focus_confirms_twice_and_never_places_order(monkeypatch):
    from test_dex_virtual_paper import source
    monkeypatch.setenv("DEX_REFERENCE_FOCUS", "true")
    monkeypatch.delenv("DEX_CHANNEL_SIGNALS_ONLY", raising=False)
    async def go():
        s, r, public, provider, now = source("reference")
        # Four dollars with 4% gross edge passes the risk/reward floor.
        first = await s.entry(r)
        assert first["reason"] == "DEX_REFERENCE_CONFIRMATION_REQUIRED", first
        now[0] += 5
        second = await s.entry(r)
        assert second["ok"], second
        public.bid = 1.001
        failed = await s.entry(r)
        assert not failed["ok"]
        public.bid = 1.04
        now[0] += 5
        assert (await s.entry(r))["reason"] == "DEX_REFERENCE_CONFIRMATION_REQUIRED"
    asyncio.run(go())


def test_missing_signal_does_not_call_market(monkeypatch):
    from test_dex_virtual_paper import source
    monkeypatch.setenv("DEX_CHANNEL_SIGNALS_ONLY", "true")
    async def go():
        s, r, public, provider, now = source("reference")
        assert (await s.entry(r))["reason"] == "DEX_CHANNEL_SIGNAL_REQUIRED"
        assert not provider.calls
    asyncio.run(go())
