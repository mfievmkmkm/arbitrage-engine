from openpyxl import Workbook
from pathlib import Path
from .secret_redaction import redact
def write(path,sheets):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);wb=Workbook();wb.remove(wb.active)
 for name,rows in sheets.items():
  ws=wb.create_sheet(str(name)[:31]);rows=redact(rows)
  if not rows:continue
  keys=sorted(set().union(*(r.keys() for r in rows)));ws.append(keys)
  for r in rows:ws.append([str(r.get(k,"")) if isinstance(r.get(k),(dict,list,tuple)) else r.get(k,"") for k in keys])
  ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
 wb.save(p);return str(p)
