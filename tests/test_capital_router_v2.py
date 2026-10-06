from app.capital_router_v2 import allocate
def test_router_keeps_large_reserve_and_dex_zero():
 x=allocate(50,{"a":2,"b":1});assert x["reserve"]>=30 and x["allocated"]["cex_dex"]==0 and x["venues"][0]=="a"
