import asyncio, time, logging


def live_dispatch_enabled(config, strategy):
    """Paper toggles do not request LIVE entry. This is not execution authority.

    Existing positions are monitored separately; private/exchange gates still
    run inside each coordinator when LIVE entry was explicitly configured.
    """
    if not config.live_enabled or not config.live_entry_enabled:
        return False
    flag = {'spot_futures': 'live_spot_futures_enabled',
            'spot_spot': 'live_spot_spot_enabled',
            'funding_arb': 'live_funding_enabled'}.get(strategy)
    return bool(getattr(config, flag, False)) if flag else strategy == 'dex_live_candidates'


class SecondaryRuntime:
    def __init__(self, runtime, diary_record, db_path, interval=30):
        self.runtime = runtime
        self.record = diary_record
        self.db_path = db_path
        self.interval = interval
        self.services = {}
        self.tasks = []
        self.running = False
        self.paused = lambda: False
        self.on_rows = None

    def add(self, name, service):
        self.services[name] = service

    @staticmethod
    def idle_paper(service):
        paper=getattr(service,'paper',None)
        if paper is None:return True
        # Unknown module shapes continue running rather than abandoning risk.
        if hasattr(paper,'positions'):return not paper.positions
        inner=getattr(paper,'sf',None)
        if inner is not None and hasattr(inner,'positions'):return not inner.positions
        return False

    async def wait(self, service):
        event=getattr(service,'wake',None)
        interval=getattr(service,'interval',self.interval)
        if event is None:
            return await asyncio.sleep(interval)
        try:
            await asyncio.wait_for(event.wait(),timeout=interval)
        except asyncio.TimeoutError:
            pass
        event.clear()

    async def _loop(self, name, service):
        while self.running:
            try:
                service.entry_enabled = (
                    self.runtime.enabled.get(getattr(service, 'enabled_name', name), True) and not self.paused()
                )
                if not service.entry_enabled and (not getattr(service, "paper", None)
                        or (getattr(service,'idle_when_disabled',False) and self.idle_paper(service))):
                    self.runtime.update(name, [])
                    await self.wait(service)
                    continue
                started=time.perf_counter()
                try:
                    rows = await service.cycle()
                finally:
                    service.last_cycle_seconds=time.perf_counter()-started
                    service.last_cycle_at=time.time()
                    if name in ('dex_paired','dex_paired_scan','dex_paired_catalog','market_signals') and time.monotonic()-getattr(service,'last_timing_log',0)>=60:
                        logging.getLogger('arbitrage').info('PAPER_SCAN | %s | cycle_ms=%.0f | entries=%s',
                            name,service.last_cycle_seconds*1000,service.entry_enabled)
                        service.last_timing_log=time.monotonic()
                ts = time.time()
                self.runtime.update(name, rows if service.entry_enabled else [])
                obs = []
                for x in rows:
                    y = dict(x) if isinstance(x, dict) else dict(x.__dict__)
                    y.setdefault("strategy", name)
                    y.setdefault("ts", ts)
                    obs.append(
                        {
                            "ts": y["ts"],
                            "strategy": name,
                            "symbol": y.get("symbol") or y.get("base", ""),
                            "venue": y.get("exchange")
                            or y.get("buy")
                            or y.get("long_venue", ""),
                            "edge": y.get(
                                "hypothetical_edge", y.get("net", y.get("carry_pct", 0))
                            ),
                            "notional": y.get("notional", 0),
                            "payload": y,
                        }
                    )
                await self.record(self.db_path, obs)
                if self.on_rows:
                    await self.on_rows(name, rows, service.entry_enabled)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.runtime.fail(name)
                logging.getLogger("arbitrage").exception(
                    "Secondary strategy failed: %s", name
                )
            await self.wait(service)

    async def start(self):
        self.running = True
        self.tasks = [
            asyncio.create_task(self._loop(n, s)) for n, s in self.services.items()
        ]

    async def stop(self):
        self.running = False
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
