def split(rows,train_ratio=.7):
 n=max(0,min(len(rows),int(len(rows)*train_ratio)));return rows[:n],rows[n:]
