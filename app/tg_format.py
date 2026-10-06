from datetime import datetime,timezone

def money(x):return f"{x:+.4f} USDT"
def pct(x):return f"{x:+.3f}%"
def age(ts):return datetime.fromtimestamp(ts,timezone.utc).strftime("%H:%M:%S UTC") if ts else "—"
def bar(value,total,width=10):
 n=0 if total<=0 else max(0,min(width,round(width*value/total)));return "▰"*n+"▱"*(width-n)
def state(ok):return "● ONLINE" if ok else "● OFFLINE"
