class StrategyRuntime:
    def __init__(self):
        self.latest = {}
        self.enabled = {
            "futures_futures": True,
            "spot_futures": True,
            "spot_spot": True,
            "funding_arb": True,
            "cex_dex": False,
        }

    def update(self, name, rows):
        self.latest[name] = rows

    def top(self, name, n=8):
        return self.latest.get(name, [])[:n]

    def counts(self):
        return {k: len(v) for k, v in self.latest.items()}

    def fail(self, name):
        self.latest[name] = []
        if not hasattr(self, "errors"):
            self.errors = {}
        self.errors[name] = self.errors.get(name, 0) + 1
