def render(rows):
 lines=["📊 STRATEGY STATS"]
 for x in rows:lines.append("%s • obs %s • avg %.3f%% • best %.3f%%"%(x["strategy"],x["observations"],x["avg_edge"] or 0,x["best_edge"] or 0))
 return "\n".join(lines)
