import asyncio
from dataclasses import dataclass
@dataclass(frozen=True)
class Balance:
 venue:str
 currency:str
 free:float
 used:float
 total:float
async def read_usdt(venue,client,timeout=8):
 row=await asyncio.wait_for(client.fetch_balance(),timeout=timeout)
 free=(row.get("free") or {}).get("USDT")
 used=(row.get("used") or {}).get("USDT")
 total=(row.get("total") or {}).get("USDT")
 usdt=row.get("USDT") or {}
 free=float(free if free is not None else usdt.get("free") or 0)
 used=float(used if used is not None else usdt.get("used") or 0)
 total=float(total if total is not None else usdt.get("total") or free+used)
 return Balance(venue,"USDT",free,used,total)
