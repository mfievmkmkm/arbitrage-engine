from app.execution_pipeline import pre_submit
class G:micro_live=True
def test_stale_books_block_even_when_live_ready():
 x=pre_submit(True,G(),True,True,True,50,5,0,books_fresh=False)
 assert not x.allowed and "MARKET_DATA_STALE" in x.reasons
def test_ready():
 assert pre_submit(True,G(),True,True,True,50,5,0).allowed

def test_unknown_net_edge_blocks_when_threshold_required():
 x=pre_submit(True,G(),True,True,True,50,5,0,min_net_edge_usd=.1)
 assert not x.allowed and "NET_EDGE_UNKNOWN" in x.reasons
def test_low_net_edge_blocks():
 x=pre_submit(True,G(),True,True,True,50,5,0,net_edge_usd=.05,min_net_edge_usd=.1)
 assert not x.allowed and "NET_EDGE_TOO_LOW" in x.reasons
