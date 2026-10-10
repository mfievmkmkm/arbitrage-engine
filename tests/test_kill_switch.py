from app.kill_switch import KillSwitch
def test_kill_switch_scopes():
 k=KillSwitch();assert not k.check("X","a","b").blocked
 k.trip_pair("X","BAD_BOOK");assert k.check("X","a","b").blocked
 k=KillSwitch();k.trip_venue("b","API_ERRORS");assert k.check("X","a","b").scope=="VENUE:b"
 k.trip_global("STATE_UNTRUSTED");assert k.check("X","a","b").scope=="GLOBAL"
