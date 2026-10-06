from app.funding_window import check
def test_adverse_imminent_funding_blocks_entry():
 assert not check(100,150,-.1,.2,120).safe
 assert check(100,500,-.1,.2,120).safe
