from app.recovery_matrix import standard_cases,evaluate
def test_matrix():
 x={c.name:evaluate(c) for c in standard_cases()}
 assert x["balanced"].action=="HEDGED"
 assert (x["long_heavy_complete"].venue,x["long_heavy_complete"].side)==("short","sell")
 assert (x["long_heavy_flatten"].venue,x["long_heavy_flatten"].side)==("long","sell")
 assert (x["short_heavy_complete"].venue,x["short_heavy_complete"].side)==("long","buy")
 assert (x["short_heavy_flatten"].venue,x["short_heavy_flatten"].side)==("short","buy")
 assert x["edge_gone_long"].action=="FLATTEN" and x["edge_gone_short"].action=="FLATTEN"
