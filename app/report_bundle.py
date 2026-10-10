from pathlib import Path
from .export_service import write_csv
def build(directory,observations,trades,ledger,replay):
 d=Path(directory);d.mkdir(parents=True,exist_ok=True);return {"observations":write_csv(d/"observations.csv",observations),"trades":write_csv(d/"trades.csv",trades),"ledger":write_csv(d/"ledger.csv",ledger),"replay":write_csv(d/"replay.csv",replay)}
