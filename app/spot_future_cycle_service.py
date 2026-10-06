import json
import time
from . import spot_future_paper_store as storage
from .spot_future_paper import Position


class CycleService:
    def __init__(self, service, paper=None, entry_edge=1, db_path=None):
        self.service = service
        self.paper = paper
        self.entry_edge = entry_edge
        self.db_path = db_path
        self.entry_enabled = True
        self.allow_open = lambda x: True
        self.on_closed = None

    async def restore(self):
        if not self.db_path or not self.paper:
            return
        await storage.init(self.db_path)
        rows = await storage.all_rows(self.db_path)
        self.paper.sf.next_id = max([x["id"] for x in rows], default=0) + 1
        for x in rows:
            p = Position(**json.loads(x["payload"]))
            if p.status == "OPEN":
                self.paper.sf.positions[p.id] = p

    async def cycle(self):
        self.service.source.watch_pairs = (
            {(p.exchange, p.base) for p in self.paper.sf.positions.values()}
            if self.paper
            else set()
        )
        rows, _ = await self.service.cycle()
        if self.paper:
            # Reverse direction requires borrowing evidence. Research quotes remain visible.
            opened, closed = self.paper.spot_future(
                rows,
                self.entry_edge,
                lambda x: self.entry_enabled and self.allow_open(x),
            )
            if self.db_path:
                current = {(x["exchange"], x["base"]) for x in rows}
                for p in list(self.paper.sf.positions.values()) + closed:
                    if (p.exchange, p.base) not in current:
                        continue
                    await storage.save(self.db_path, p)
                    await storage.mark(self.db_path, p, time.time())
            if self.on_closed:
                for p in closed:
                    await self.on_closed(p)
        return rows
