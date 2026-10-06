from pathlib import Path
from .export_query import observations,ledger
from .xlsx_export import write
async def build(db_path,out_dir):
 obs=await observations(db_path);led=await ledger(db_path);p=Path(out_dir)/"arbitrage_report.xlsx";return write(p,{"Observations":obs,"Ledger":led})
