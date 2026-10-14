def build(stats,replay,incidents,venue_scores):
 return {"stats":stats,"replay":replay,"incidents":incidents[-50:],"venue_scores":venue_scores,"rule":"recommend_only_no_execution"}
