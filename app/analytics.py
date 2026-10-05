from collections import defaultdict
def venue_routes(observations):
    stats=defaultdict(lambda:{"count":0,"sum":0.0,"max":float("-inf")})
    for o in observations:
        key=o["buy"]+" -> "+o["sell"];r=stats[key];r["count"]+=1;r["sum"]+=o["hypothetical_edge"];r["max"]=max(r["max"],o["hypothetical_edge"])
    rows=[{"route":k,"count":v["count"],"avg_edge":v["sum"]/v["count"],"max_edge":v["max"]} for k,v in stats.items()]
    return sorted(rows,key=lambda x:(x["count"],x["avg_edge"]),reverse=True)
def compact_research_snapshot(replay_rows,route_rows,health):
    return {"schema":"arbitrage-research-v1","replay_candidates":replay_rows[:5],"venue_routes":route_rows[:10],"venue_health":health,"mode":"research_only"}
