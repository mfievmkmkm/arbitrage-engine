from app.exit_plan import build
def test_exit_is_reduce_only():
 x=build("X",10,1)
 assert x.long_close.side=="sell" and x.short_close.side=="buy"
 assert x.long_close.reduce_only and x.short_close.reduce_only
