import aiosqlite
SCHEMA="CREATE TABLE IF NOT EXISTS operator_events(ts REAL,user_id INTEGER,event TEXT,payload TEXT);"
async def init(path):
 async with aiosqlite.connect(path) as d:await d.execute(SCHEMA);await d.commit()
async def add(path,ts,user,event,payload=""):
 async with aiosqlite.connect(path) as d:await d.execute("INSERT INTO operator_events VALUES(?,?,?,?)",(ts,user,event,payload));await d.commit()
