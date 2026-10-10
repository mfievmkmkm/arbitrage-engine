import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FeeRate:
    maker: float
    taker: float


class FeeSchedule:
    def __init__(self, rates=None):
        self.rates = {str(k).lower(): v for k, v in (rates or {}).items()}

    def rate(self, venue, liquidity="taker"):
        x = self.rates.get(str(venue).lower())
        if x is None:
            return None
        return x.maker if liquidity == "maker" else x.taker

    def require(self, venue, liquidity="taker"):
        r = self.rate(venue, liquidity)
        try:
            if r is None or isinstance(r, bool):
                raise ValueError()
            r = float(r)
            if not math.isfinite(r) or r < 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise RuntimeError("UNKNOWN_FEE_RATE:" + str(venue))
        return r


DEFAULT_FUTURES_FEES = FeeSchedule(
    {
        "binance": FeeRate(0.0002, 0.0005),
        "bybit": FeeRate(0.0002, 0.00055),
        "okx": FeeRate(0.0002, 0.0005),
        "mexc": FeeRate(0.0001, 0.0004),
        "bitget": FeeRate(0.0002, 0.0006),
        "gate": FeeRate(0.0002, 0.0005),
    }
)
