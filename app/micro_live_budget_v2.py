def allow(bankroll,open_notional,request_notional,daily_loss_pct,max_open_pct=.15,max_trade_pct=.08,daily_stop_pct=2):
 if bankroll<=0:return False,"BANKROLL"
 if daily_loss_pct>=daily_stop_pct:return False,"DAILY_STOP"
 if request_notional>bankroll*max_trade_pct:return False,"TRADE_SIZE"
 if open_notional+request_notional>bankroll*max_open_pct:return False,"OPEN_EXPOSURE"
 return True,"OK"
