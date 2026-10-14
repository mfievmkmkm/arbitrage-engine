def freshness(book,now,max_age):
 ts=book.get("timestamp")
 if ts is None:return False
 if ts>1e12:ts/=1000
 return now-ts<=max_age

def sane(book):
 try:return bool(book["bids"] and book["asks"] and float(book["bids"][0][0])>0 and float(book["asks"][0][0])>=float(book["bids"][0][0]))
 except Exception:return False
