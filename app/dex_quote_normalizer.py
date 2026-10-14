from .dex_quote import DexQuote
def normalize(chain,token_in,token_out,amount_in,row):
 try:return DexQuote(chain,token_in,token_out,float(amount_in),float(row["amount_out"]),float(row["gas_usd"]),float(row["price_impact_pct"]),str(row.get("route","")))
 except Exception:return DexQuote(chain,token_in,token_out,float(amount_in),0,-1,-1,"")
