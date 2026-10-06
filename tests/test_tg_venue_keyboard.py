from app.tg_venue_keyboard import control
def test_venue_keyboard_shows_current_modes():
 x=[b.text for r in control("a",{"scan":True,"paper":False,"real":False}).inline_keyboard for b in r];assert "SCAN · ON" in x and "REAL · OFF" in x
