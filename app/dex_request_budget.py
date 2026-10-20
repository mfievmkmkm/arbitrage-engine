"""Shared read-only request admission; scans yield to position/accounting IO."""
import asyncio
import time
from contextvars import ContextVar

background = ContextVar('dex_background', default=False)


class Budget:
    def __init__(self, spacing=.26, concurrency=4):
        self.spacing = spacing
        self.slots = asyncio.Semaphore(concurrency)
        self.scans = asyncio.Semaphore(max(1, concurrency-2))
        self.lock = asyncio.Lock()
        self.next_at = 0
        self.foreground = 0

    async def acquire(self):
        low = background.get()
        if low:
            await self.scans.acquire()
        else:
            self.foreground += 1
        acquired = False
        try:
            while True:
                async with self.lock:
                    now = time.monotonic()
                    if now >= self.next_at and (not low or not self.foreground):
                        await self.slots.acquire()
                        acquired = True
                        self.next_at = time.monotonic()+self.spacing
                        return low
                    delay = max(.01, min(.1, self.next_at-now))
                await asyncio.sleep(delay)
        finally:
            if not low:
                self.foreground -= 1
            if low and not acquired:
                self.scans.release()

    def release(self, low):
        self.slots.release()
        if low:
            self.scans.release()
