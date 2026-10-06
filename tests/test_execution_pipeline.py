from app.execution_pipeline import pre_submit
class G:micro_live=True
def test_stale_books_block_even_when_live_ready():
 x=pre_submit(True,G(),True,True,True,50,5,0,books_fresh=False)
 assert not x.allowed and "MARKET_DATA_STALE" in x.reasons
def test_ready():
 assert pre_submit(True,G(),True,True,True,50,5,0).allowed
