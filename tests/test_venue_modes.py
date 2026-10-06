from app.venue_modes import Modes
def test_real_mode_requires_certification_and_acceptance():
 x=Modes();assert not x.enable_real(True,False);assert x.enable_real(True,True)
