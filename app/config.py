import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    token: str = os.getenv("BOT_TOKEN", "")
    admin_id: int = int(os.getenv("ADMIN_ID", "0"))
    db_path: str = os.getenv("DB_PATH", "arbitrage.sqlite3")
    interval: int = max(15, int(os.getenv("SCAN_INTERVAL", "30")))
    notional: float = max(1, float(os.getenv("NOTIONAL_USD", "5")))
    max_age: float = max(2, float(os.getenv("MAX_BOOK_AGE_SEC", "12")))
    universe_size: int = max(20, int(os.getenv("UNIVERSE_SIZE", "120")))
    scan_batch_size: int = max(10, int(os.getenv("SCAN_BATCH_SIZE", "30")))
    scan_concurrency: int = max(2, int(os.getenv("SCAN_CONCURRENCY", "8")))
    min_edge: float = float(os.getenv("MIN_ESTIMATED_EDGE_PCT", "1.5"))
    safety_buffer_pct: float = max(0, float(os.getenv("SAFETY_BUFFER_PCT", "0.10")))
    daily_stop_pct: float = max(0.1, float(os.getenv("DAILY_STOP_PCT", "2.0")))
    max_engine_errors: int = max(1, int(os.getenv("MAX_ENGINE_ERRORS", "3")))
    live_enabled: bool = os.getenv("LIVE_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    live_reconcile_interval: float = max(
        5, float(os.getenv("LIVE_RECONCILE_INTERVAL_SEC", "10"))
    )
    runtime_state_path: str = os.getenv("RUNTIME_STATE_PATH", "runtime_state.json")
    live_max_open_trades: int = max(1, int(os.getenv("LIVE_MAX_OPEN_TRADES", "1")))
    live_max_book_age_ms: int = max(100, int(os.getenv("LIVE_MAX_BOOK_AGE_MS", "1500")))
    live_max_slippage_pct: float = max(
        0, float(os.getenv("LIVE_MAX_SLIPPAGE_PCT", "0.20"))
    )
    live_min_net_edge_usd: float = max(
        0, float(os.getenv("LIVE_MIN_NET_EDGE_USD", "0.05"))
    )
    live_no_withdraw_attested: bool = os.getenv(
        "LIVE_NO_WITHDRAW_ATTESTED", "false"
    ).strip().lower() in ("1", "true", "yes", "on")
    live_metrics_path: str = os.getenv("LIVE_METRICS_PATH", "live_metrics.json")
    paper_capital: float = max(10, float(os.getenv("PAPER_CAPITAL_USD", "50")))
    max_paper_positions: int = max(1, int(os.getenv("MAX_PAPER_POSITIONS", "2")))
    paper_entry_edge: float = float(os.getenv("PAPER_ENTRY_EDGE_PCT", "2.0"))
    paper_target_convergence: float = float(
        os.getenv("PAPER_TARGET_CONVERGENCE", "0.70")
    )
    paper_trailing_drawdown: float = float(os.getenv("PAPER_TRAILING_DRAWDOWN", "0.20"))
    paper_max_seconds: int = max(60, int(os.getenv("PAPER_MAX_SECONDS", "1200")))
    exchanges: tuple[str, ...] = tuple(
        x.strip()
        for x in os.getenv(
            "EXCHANGES", "binance,bybit,okx,bitget,gateio,mexc,bingx"
        ).split(",")
        if x.strip()
    )


config = Config()
