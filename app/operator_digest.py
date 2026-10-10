def render(stats,anomalies,recommendations=()):
 return "📊 OPERATOR DIGEST\n"+f"Trades: {stats.get('trades',0)} • NET: {stats.get('net',0):+.4f}$\n"+("Anomalies: "+", ".join(anomalies) if anomalies else "Anomalies: none")+"\nRecommendations: "+("; ".join(recommendations) if recommendations else "none")
