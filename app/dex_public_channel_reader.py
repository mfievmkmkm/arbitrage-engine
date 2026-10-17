"""Read public Telegram previews without account, session, cookie or forwarding."""
import asyncio
import json
from datetime import datetime
from html.parser import HTMLParser
import aiohttp
import aiosqlite

CHANNEL = "MexcDexSpreadTracker"
URL = "https://t.me/s/" + CHANNEL


class Posts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.current, self.text_depth = 0, None, None
        self.root_depth = None
        self.posts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            self.depth += 1
            classes = a.get("class", "").split()
            if "tgme_widget_message" in classes:
                post = a.get("data-post", "")
                if post.startswith(CHANNEL + "/") and post.split("/")[-1].isdigit():
                    self.current = dict(id=int(post.split("/")[-1]), text="", timestamp=None)
                    self.root_depth = self.depth
            if self.current and "tgme_widget_message_text" in classes:
                self.text_depth = self.depth
        if self.current and tag == "time" and a.get("datetime"):
            try:
                date = datetime.fromisoformat(a["datetime"].replace("Z", "+00:00"))
                if date.tzinfo:
                    self.current["timestamp"] = date.timestamp()
            except ValueError:
                pass
        if tag == "br" and self.current and self.text_depth:
            self.current["text"] += "\n"

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.current and self.depth == self.text_depth:
            self.text_depth = None
        if self.current and self.depth == self.root_depth:
            if self.current["timestamp"] is not None and self.current["text"]:
                self.posts.append(self.current)
            self.current, self.root_depth, self.text_depth = None, None, None
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.current and self.text_depth and len(self.current["text"]) < 20000:
            self.current["text"] += data


def parse_posts(html):
    parser = Posts()
    parser.feed(html)
    return sorted(parser.posts, key=lambda p: p["id"])


class Reader:
    def __init__(self, engine, enabled, fetch=None, on_post=None, report_only=False):
        self.engine, self.enabled, self.fetch = engine, enabled, fetch
        self.task = None
        self.status = "Ожидает запуска публичного чтения"
        self.seen = set()
        self.metrics = dict(received=0, accepted=0, last="Нет новых сигналов")
        self.on_post = on_post
        self.report_only = report_only

    async def start(self):
        async with aiosqlite.connect(self.engine.path) as d:
            await d.execute("CREATE TABLE IF NOT EXISTS dex_channel_reader_state(id INTEGER PRIMARY KEY,payload TEXT)")
            async with d.execute("SELECT payload FROM dex_channel_reader_state WHERE id=1") as c:
                row = await c.fetchone()
                if row:
                    saved = json.loads(row[0])
                    self.seen = set(saved["seen"])
                    self.metrics = saved["metrics"]
            await d.commit()
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def accept(self, post):
        if post["id"] in self.seen or not self.enabled():
            return
        # Queue durably before marking seen. Reports are independent of legacy
        # strategy admission, malformed routes, and a later Telegram outage.
        if self.on_post and ("SHORT?" in post['text'] or "LONG?" in post['text']):
            await self.on_post(post)
        self.seen.add(post["id"])
        if len(self.seen) > 2000:
            self.seen = set(sorted(self.seen)[-1000:])
        if "SHORT?" not in post["text"] and "LONG?" not in post["text"]:
            return
        detail = dict(channel=CHANNEL, message_id=post["id"], timestamp=post["timestamp"], source="PUBLIC_TELEGRAM_PREVIEW")
        reason = "DEX_CHANNEL_PUBLIC_ACCEPTED"
        try:
            if self.report_only:
                reason = 'DEX_CHANNEL_REVIEW_QUEUED'
                self.metrics['last'] = 'Сообщение поставлено в очередь независимой проверки'
            else:
                resolver = getattr(self.engine.source, "resolve_signal", None)
                if resolver:
                    await resolver(post["text"], post["timestamp"])
                s = self.engine.source.signals.add(post["text"], self.engine.source.routes, post["timestamp"], "PUBLIC_TELEGRAM_PREVIEW:" + CHANNEL)
                detail.update(base=s["base"], side=s["side"], chain_id=s["chain_id"], asset=s["asset"])
                self.status = "Читает публично · последний сигнал " + s["base"]
                self.metrics["accepted"] += 1
                self.metrics["last"] = s["base"] + " · принят для проверки входа"
        except ValueError as exc:
            reason = "DEX_CHANNEL_PUBLIC_SKIPPED"
            detail["reason"] = str(exc)
            from .dex_signal_focus import parse_signal
            try:
                base = parse_signal(post["text"])["base"]
            except ValueError:
                base = "Сигнал"
            self.metrics["last"] = base + " · пропущен: " + str(exc)
        self.metrics["received"] += 1
        self.metrics["last_post"] = post["id"]
        self.metrics["last_at"] = self.engine.clock()
        async with aiosqlite.connect(self.engine.path) as d:
            if self.report_only:
                await d.execute('CREATE TABLE IF NOT EXISTS channel_signal_posts(id INTEGER PRIMARY KEY,ts REAL,reason TEXT,payload TEXT)')
            table='channel_signal_posts' if self.report_only else 'cex_dex_paper_decisions'
            statement='INSERT INTO '+table+'(ts,reason,payload) VALUES(?,?,?)'
            await d.execute(statement if self.report_only else self.engine.sql(statement),
                            (self.engine.clock(), reason, json.dumps(detail)))
            await d.execute("CREATE TABLE IF NOT EXISTS dex_channel_reader_state(id INTEGER PRIMARY KEY,payload TEXT)")
            await d.execute("INSERT OR REPLACE INTO dex_channel_reader_state VALUES(1,?)", (json.dumps(dict(seen=sorted(self.seen), metrics=self.metrics)),))
            await d.commit()

    async def run(self):
        failures = 0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), cookie_jar=aiohttp.DummyCookieJar()) as session:
            while True:
                delay = 5
                try:
                    if not self.enabled():
                        self.status = "Публичное чтение · стратегия выключена"
                    else:
                        if self.fetch:
                            html = await self.fetch()
                        else:
                            async with session.get(URL, allow_redirects=False) as response:
                                if response.status != 200:
                                    raise ValueError("HTTP_" + str(response.status))
                                data = bytearray()
                                async for chunk in response.content.iter_chunked(65536):
                                    data.extend(chunk)
                                    if len(data) > 2000000:
                                        raise ValueError("PREVIEW_TOO_LARGE")
                                html = data.decode("utf-8")
                        posts = parse_posts(html)
                        if not posts:
                            raise ValueError("PREVIEW_NO_POSTS")
                        for post in posts:
                            await self.accept(post)
                        failures = 0
                        self.status = "Читает публичную страницу · последняя проверка успешна"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    delay = min(300, 15 * 2 ** min(failures, 4))
                    self.status = "Публичная страница недоступна: " + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
                await asyncio.sleep(delay)
