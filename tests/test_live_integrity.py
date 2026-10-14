from app.live_integrity import check
class H:ok=True
def test_unresolved_intent_blocks_integrity():
 assert not check([],{},{"x":"UNKNOWN"}).safe
def test_empty_clean_runtime_is_safe():
 assert check([],{"a":{"health":H(),"positions":[]}},{}).safe
