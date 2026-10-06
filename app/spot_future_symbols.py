from dataclasses import dataclass
@dataclass(frozen=True)
class Pair:
 base:str;spot_symbol:str;future_symbol:str

def normalize(markets):
 spots={m.get("base"):m["symbol"] for m in markets.values() if m.get("spot") and m.get("quote")=="USDT" and m.get("active") is not False}
 futures={m.get("base"):m["symbol"] for m in markets.values() if m.get("swap") and m.get("linear") and m.get("settle")=="USDT" and m.get("active") is not False}
 return [Pair(b,spots[b],futures[b]) for b in sorted(spots.keys()&futures.keys())]
