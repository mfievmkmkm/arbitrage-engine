from app.contract_book import to_base_levels
def test_book_amount_to_base():
 assert to_base_levels([[100,10]],.001)==[[100.0,.01]]
