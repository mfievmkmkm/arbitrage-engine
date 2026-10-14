from app.strategy_evidence import paper_safe
def test_paper_requires_fresh_book_verified_fee_and_funding():assert paper_safe({"book_fresh":1,"fees_verified":1,"funding_known":1}) and not paper_safe({"book_fresh":1,"fees_verified":0,"funding_known":1})
