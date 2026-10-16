"""One conservative REST depth budget per venue/type across public clients."""
import asyncio
import heapq
import time
import weakref

_loops = weakref.WeakKeyDictionary()


class Pacer:
    def __init__(self, interval=.25, clock=time.monotonic):
        self.interval, self.clock = interval, clock
        self.next_at = 0
        self._queue = []
        self._sequence = 0
        self._worker = None
        self.failures = 0

    async def _drain(self):
        while self._queue:
            delay = self.next_at - self.clock()
            if delay > 0:
                await asyncio.sleep(delay)
                # A 429 can extend the cooldown while the worker is asleep.
                continue
            _, _, ticket = heapq.heappop(self._queue)
            if ticket.cancelled():
                continue
            self.next_at = self.clock() + self.interval
            ticket.set_result(None)

    async def wait(self, priority=1):
        if priority not in (0, 1):
            raise ValueError('PUBLIC_BOOK_PRIORITY_INVALID')
        ticket = asyncio.get_running_loop().create_future()
        heapq.heappush(self._queue, (priority, self._sequence, ticket))
        self._sequence += 1
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain())
        try:
            await ticket
        finally:
            if not ticket.done():
                ticket.cancel()
            if not any(not item[2].cancelled() for item in self._queue):
                self._queue.clear()
                if self._worker and not self._worker.done():
                    self._worker.cancel()

    def limited(self):
        self.failures += 1
        self.next_at = max(self.next_at, self.clock() + min(30, 2 ** self.failures))

    def success(self):
        self.failures = 0


def get(client, symbol):
    if getattr(client, 'id', None) != 'mexc':
        return None
    pool = _loops.setdefault(asyncio.get_running_loop(), {})
    return pool.setdefault(('mexc', 'swap' if ':' in symbol else 'spot'), Pacer())
