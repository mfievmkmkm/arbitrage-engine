import asyncio
import sqlite3
from app.dex_public_channel_reader import parse_posts, Reader
from app.cex_dex_paper import Engine
from app.dex_signal_focus import Signals


def post(mid, date, text, channel="MexcDexSpreadTracker"):
    return f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="{channel}/{mid}"><div class="tgme_widget_message_text js-message_text"><b>{text}</b><br/>end &amp; ok<img src="x"></div><div class="footer"><time datetime="{date}">time</time></div></div></div>'


def test_html_scope_markup_dates_and_empty_preview():
    rows = parse_posts(post(2,"1970-01-01T00:01:40+00:00","SHORT? #X") + post(1,"1970-01-01T00:01:39Z","LONG? #X") + post(3,"1970-01-01T00:01:40Z","SHORT? #X", "other"))
    assert [p["id"] for p in rows] == [1,2]
    assert rows[1]["timestamp"] == 100
    assert rows[1]["text"] == "SHORT? #X\nend & ok"
    assert not parse_posts("Sign in")
    assert not parse_posts(post(4,"1970-01-01T00:01:40","SHORT? #X"))


def test_public_signal_freshness_scope_dedup_and_isolation(tmp_path):
    from test_dex_virtual_paper import source
    async def go():
        s,r,public,provider,now = source("reference")
        r["cex_venue"] = "mexc"
        s.signals = Signals(str(tmp_path / "signals.json"), s.clock)
        engine = Engine(tmp_path / "db", s, virtual=True, namespace="channel", clock=s.clock)
        await engine.init()
        enabled = [True]
        reader = Reader(engine, lambda: enabled[0])
        text = "SHORT? #X\n#ETHEREUM\n" + r["asset"]
        await reader.accept(dict(id=1,text=text,timestamp=100))
        await reader.accept(dict(id=1,text=text,timestamp=100))
        assert s.signals.get(r)[0]["side"] == "sell"
        await reader.accept(dict(id=2,text=text,timestamp=1))
        await reader.accept(dict(id=3,text=text+"\nAligned in 31 sec",timestamp=100))
        enabled[0] = False
        await reader.accept(dict(id=4,text=text,timestamp=100))
        with sqlite3.connect(engine.path) as c:
            rows=c.execute("SELECT reason FROM cex_dex_virtual_paper_channel_decisions ORDER BY id").fetchall()
        assert rows == [("DEX_CHANNEL_PUBLIC_ACCEPTED",),("DEX_CHANNEL_PUBLIC_SKIPPED",),("DEX_CHANNEL_PUBLIC_SKIPPED",)]
        assert not engine.positions  # A public post doesn't substitute execution checks.
    asyncio.run(go())


def test_unavailable_public_preview_never_creates_a_signal():
    from types import SimpleNamespace
    async def go():
        async def fetch():return "<html><h1>Site Unavailable</h1></html>"
        reader = Reader(SimpleNamespace(), lambda: True, fetch)
        await reader.start()
        for _ in range(5):
            await asyncio.sleep(0)
        assert "PREVIEW_NO_POSTS" in reader.status
        assert not reader.seen
        await reader.stop()
        assert reader.task is None
    asyncio.run(go())
