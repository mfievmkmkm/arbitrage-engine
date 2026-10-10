import time,json
from .operator_event_log import add
async def event(path,user,event,payload=None):await add(path,time.time(),user,event,json.dumps(payload or {},separators=(",",":")))
