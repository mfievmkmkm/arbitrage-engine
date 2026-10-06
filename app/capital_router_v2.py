def allocate(equity,venue_scores,strategy_caps=None,reserve_pct=.6):
 caps=strategy_caps or {"futures_futures":.1,"spot_futures":.05,"spot_spot":.05,"funding_arb":.05,"cex_dex":0};tradable=max(0,equity*(1-reserve_pct));out={};used=0
 for strategy,cap in caps.items():
  amount=min(equity*cap,max(0,tradable-used));out[strategy]=round(amount,8);used+=amount
 return {"equity":equity,"reserve":equity-used,"allocated":out,"venues":[x[0] for x in sorted(venue_scores.items(),key=lambda z:z[1],reverse=True)[:3]]}
