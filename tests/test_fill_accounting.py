from app.fill_accounting import closed,entry_spread
def test_actual_fills():
 x=closed(100,103,110,106,1,.5,.1)
 assert x.gross==7 and x.net==6.6 and entry_spread(100,110)==10
