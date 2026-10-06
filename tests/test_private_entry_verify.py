import asyncio
from app.private_entry_verify import verify
from app.private_adapter import PrivatePosition
from app.account_health import AccountHealth

class H:
 ok=True

def snap(long=.04,short=.04,flip=False):
 return {"a":{"health":H(),"positions":[PrivatePosition("a","X","short" if flip else "long",long)]},"b":{"health":H(),"positions":[PrivatePosition("b","X","short",short)]}}

def test_private_entry_verified():
 assert asyncio.run(verify(lambda:snap(),"X","a","b",.04,1,0)).verified

def test_private_entry_mismatch_blocks():
 x=asyncio.run(verify(lambda:snap(.03,.04),"X","a","b",.04,1,0))
 assert not x.verified and x.reason=="PRIVATE_POSITION_MISMATCH"

def test_private_entry_flipped_blocks():
 x=asyncio.run(verify(lambda:snap(flip=True),"X","a","b",.04,1,0))
 assert not x.verified and x.reason=="OPPOSITE_OR_FLIPPED_EXPOSURE"
