class Coordinator:
    def __init__(self, spot_future_engine):
        self.sf = spot_future_engine

    def spot_future(self, rows, entry_edge, allow_open=None):
        closed = []
        opened = []
        for x in rows:
            just_closed = self.sf.update(x)
            closed.extend(just_closed)
            if (
                not just_closed
                and (allow_open is None or allow_open(x))
                and x["hypothetical_edge"] >= entry_edge
                and x["direction"] == "LONG_SPOT_SHORT_FUTURE"
            ):
                p = self.sf.open(x)
                if p:
                    opened.append(p)
        return opened, closed
