"""Bounded, secret-free evidence for zero-opportunity PAPER futures scans.

No order, funding adjustment, or strategy acceptance path is modified.
"""


def summarize(diagnostics):
    d = diagnostics if isinstance(diagnostics, dict) else {}
    allowed = (
        "books_requested", "books_stale", "books_empty", "books_ok",
        "book_errors", "pairs_checked", "pairs_incompatible",
        "pairs_below_minimum", "pairs_stale_or_depth",
        "pairs_no_edge_before_funding", "funding_unknown",
    )
    counters = d.get("counters") or {}
    counts = {}
    if isinstance(counters, dict):
        for name in allowed:
            value = counters.get(name, 0)
            if name in counters and type(value) is int and value >= 0:
                counts[name] = value
        for name, value in counters.items():
            if (isinstance(name, str)
                    and (name.startswith("native_block:") or name.startswith("book_error:"))
                    and len(name) < 100 and name.replace(":", "").replace("_", "").isalnum()
                    and type(value) is int and value >= 0):
                counts[name] = value
    fields = ("symbols_selected", "venues_online", "symbols_with_books",
              "symbols_with_two_books", "candidates_returned")
    result = {key: d[key] for key in fields if type(d.get(key)) is int and d[key] >= 0}
    result["counters"] = counts
    return result


def decision_reasons(decisions, limit=12):
    """Aggregate only known, safe reason-code identifiers, never raw quotes."""
    from collections import Counter
    import re
    counts = Counter()
    for row in decisions:
        if not isinstance(row, dict) or row.get('paired_paper') is not True:
            continue
        reason = row.get('reason')
        if not isinstance(reason, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', reason):
            reason = 'OTHER_NON_CODE_REASON'
        counts[reason] += 1
    return dict(counts.most_common(limit))
