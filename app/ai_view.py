def render(log):
 rows=log.rows[-5:];return "🧠 AI ANALYSIS\nExecution authority: NO\n"+("\n".join("%s • replay=%s • approved=%s"%(x["text"],x["replay_validated"],x["approved"]) for x in rows) if rows else "Рекомендаций пока нет.")
