def render(t):
 return "📈 LIVE POSITION\n%s\nLONG %s / SHORT %s\nQty: %s/%s\nState: OPEN"%(t.symbol,t.long_venue,t.short_venue,t.long_contracts,t.short_contracts)
