def simulate(gross_usd,quote,cex_fee,dex_fee,slippage,safety):
 costs=cex_fee+dex_fee+quote.gas_usd+slippage+safety;return {"gross":gross_usd,"gas":quote.gas_usd,"costs":costs,"net":gross_usd-costs,"route":quote.route}
