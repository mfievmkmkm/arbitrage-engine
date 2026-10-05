import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Config:
    token: str = os.getenv("BOT_TOKEN", "")
    admin_id: int = int(os.getenv("ADMIN_ID", "0"))
    db_path: str = os.getenv("DB_PATH", "arbitrage.sqlite3")
    interval: int = max(15, int(os.getenv("SCAN_INTERVAL", "45")))
    notional: float = max(1, float(os.getenv("NOTIONAL_USD", "5")))
    max_age: float = max(2, float(os.getenv("MAX_BOOK_AGE_SEC", "12")))
    min_edge: float = float(os.getenv("MIN_ESTIMATED_EDGE_PCT", "1.5"))
    exchanges: tuple[str, ...] = tuple(x.strip() for x in os.getenv(
        "EXCHANGES", "binance,bybit,okx,bitget,gateio,mexc,bingx"
    ).split(",") if x.strip())

config = Config()
