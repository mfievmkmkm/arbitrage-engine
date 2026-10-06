from app.incident_priority import highest
def test_unknown_order_is_highest_priority():assert highest(["MARKET_DATA","UNKNOWN_ORDER","DAILY_STOP"])=="UNKNOWN_ORDER"
