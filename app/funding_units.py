def to_pct(rate):return None if rate is None else float(rate)*100.0
def same_unit(rate_a,rate_b):return to_pct(rate_a),to_pct(rate_b)
