"""One conservative REST depth budget per venue/type across public clients."""
import asyncio
import time
import weakref

_loops = weakref.WeakKeyDictionary()


class Pacer:
    def __init__(self, interval=.25, clock=time.monotonic):
        self.interval, self.clock = interval, clock
        self.next_at = 0
        self.lock = asyncio.Lock()
        self.failures = 0

    async def wait(self):
        async with self.lock:
            delay = self.next_at - self.clock()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_at = self.clock() + self.interval

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
