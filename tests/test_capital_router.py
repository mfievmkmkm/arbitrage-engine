from app.capital_router import choose
def test_router_selects_best_two_funded_venues():
 x=choose({"a":10,"b":8,"c":20},{"a":5,"b":5,"c":1},3);assert x.allowed and x.venues==("a","b")
