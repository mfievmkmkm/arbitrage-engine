from app.private_residual import verify
from app.private_adapter import PrivatePosition
class H:ok=True
def test_residual():
 s={"a":{"health":H(),"positions":[PrivatePosition("a","X","long",.1)]},"b":{"health":H(),"positions":[]}}
 assert not verify(s,"X","a","b").flat
