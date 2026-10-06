import time
from .spot_future_paper import open_from, mark
from .spot_future_exit import decide


class Engine:
    def __init__(self, max_positions=2, max_age=600, capital=50, reserved=None):
        self.positions = {}
        self.next_id = 1
        self.max_positions = max_positions
        self.max_age = max_age
        self.closed = []
        self.capital = capital
        self.reserved = reserved or (lambda: 0)

    @property
    def used_capital(self):
        return sum(p.notional * 2 for p in self.positions.values())

    def can_open(self, op):
        return (
            len(self.positions) < self.max_positions
            and op["base"] not in {p.base for p in self.positions.values()}
            and self.used_capital + self.reserved() + op["notional"] * 2 <= self.capital
        )

    def open(self, op):
        if not self.can_open(op):
            return None
        p = open_from(op, self.next_id)
        self.next_id += 1
        self.positions[p.id] = p
        return p

    def update(self, op, now=None):
        now = time.time() if now is None else now
        closed = []
        for i, p in list(self.positions.items()):
            if p.base != op["base"] or p.exchange != op["exchange"]:
                continue
            n = mark(p, op)
            x = decide(n, p.best_net, now - p.opened_at, self.max_age)
            if x.close:
                p.status = x.reason
                closed.append(p)
                self.closed.append(p)
                self.positions.pop(i)
        return closed
