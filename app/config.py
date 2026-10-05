import os
from dataclasses import dataclass
from dotenv import load_dotenv
load_dotenv()
@dataclass(frozen=True)
class Config:
 token:str=os.getenv("BOT_TOKEN","")
 admin_id:int=int(os.getenv("ADMIN_ID","0"))
 db_path:str=os.getenv("DB_PATH","arbitrage.sqlite3")
 interval:int=max(15,int(os.getenv("SCAN_INTERVAL","30")))
 notional:float=max(1,float(os.getenv("NOTIONAL_USD","5")))
 max_age:float=max(2,float(os.getenv("MAX_BOOK_AGE_SEC","12")))
 universe_size:int=max(20,int(os.getenv("UNIVERSE_SIZE","120")))
 scan_batch_size:int=max(10,int(os.getenv("SCAN_BATCH_SIZE","30")))
 scan_concurrency:int=max(2,int(os.getenv("SCAN_CONCURRENCY","8")))
 min_edge:float=float(os.getenv("MIN_ESTIMATED_EDGE_PCT","1.5"))
 paper_capital:float=max(10,float(os.getenv("PAPER_CAPITAL_USD","50")))
 max_paper_positions:int=max(1,int(os.getenv("MAX_PAPER_POSITIONS","2")))
 paper_entry_edge:float=float(os.getenv("PAPER_ENTRY_EDGE_PCT","2.0"))
 paper_target_convergence:float=float(os.getenv("PAPER_TARGET_CONVERGENCE","0.70"))
 paper_trailing_drawdown:float=float(os.getenv("PAPER_TRAILING_DRAWDOWN","0.20"))
 paper_max_seconds:int=max(60,int(os.getenv("PAPER_MAX_SECONDS","1200")))
 exchanges:tuple[str,...]=tuple(x.strip() for x in os.getenv("EXCHANGES","binance,bybit,okx,bitget,gateio,mexc,bingx").split(",") if x.strip())
config=Config()
