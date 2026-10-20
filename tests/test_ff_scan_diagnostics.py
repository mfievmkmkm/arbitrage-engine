from app.ff_scan_diagnostics import summarize


def test_report_reveals_futures_rejection_funnel_not_raw_book_or_secrets():
    details = {
        "symbols_selected": 24,
        "venues_online": 7,
        "symbols_with_two_books": 11,
        "candidates_returned": 0,
        "symbols_scanned": ["private-irrelevant-symbol"],
        "api_key": "SECRET",
        "counters": {
            "books_requested": 78,
            "books_ok": 70,
            "pairs_checked": 50,
            "pairs_no_edge_before_funding": 45,
            "funding_unknown": 2,
            "native_block:STEP_TOO_SMALL": 3,
            "other_details": "SECRET",
        },
    }
    safe = summarize(details)
    assert safe["counters"]["pairs_no_edge_before_funding"] == 45
    assert safe["counters"]["native_block:STEP_TOO_SMALL"] == 3
    assert safe["symbols_with_two_books"] == 11
    assert "private-irrelevant-symbol" not in str(safe)
    assert "SECRET" not in str(safe)


def test_empty_and_invalid_diagnostics_are_safe():
    assert summarize(None) == {"counters": {}}
    assert "api_key" not in summarize({"api_key": "SECRET", "counters": {"pairs_checked": "broken"}})
