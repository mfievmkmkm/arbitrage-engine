from app.private_residual import verify
from app.private_adapter import PrivatePosition

class H:ok=True

def test_flipped_long_venue_short_is_not_flat():
 s={
  "a":{"health":H(),"positions":[PrivatePosition("a","X","short",.1)]},
  "b":{"health":H(),"positions":[]},
 }
 r=verify(s,"X","a","b")
 assert not r.flat and r.reason=="OPPOSITE_OR_FLIPPED_EXPOSURE"

def test_flipped_short_venue_long_is_not_flat():
 s={
  "a":{"health":H(),"positions":[]},
  "b":{"health":H(),"positions":[PrivatePosition("b","X","long",.2)]},
 }
 r=verify(s,"X","a","b")
 assert not r.flat and r.reason=="OPPOSITE_OR_FLIPPED_EXPOSURE"

def test_any_symbol_exposure_blocks_flat():
 s={
  "a":{"health":H(),"positions":[PrivatePosition("a","X","long",.1),PrivatePosition("a","Y","long",5)]},
  "b":{"health":H(),"positions":[]},
 }
 assert not verify(s,"X","a","b").flat
