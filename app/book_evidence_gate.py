def evaluate(received_ms,now_ms,max_age_ms,bids,asks):
 if not bids or not asks:return False,"BOOK_EMPTY"
 if received_ms is None:return False,"BOOK_TIMESTAMP_UNKNOWN"
 if now_ms-received_ms>max_age_ms:return False,"BOOK_STALE"
 return True,"OK"
