from app.venue_controller import Controller
class S:
 def __init__(self):self.x={}
 def load(self):return self.x
 def save(self,x):self.x=x
def test_real_mode_cannot_toggle_without_acceptance():
 c=Controller(S(),["a"]);m,ok,r=c.toggle("a","real",True,False);assert not ok and not m["real"]
