from app.venue_error_circuit import VenueErrors
def test_bad_venue_isolated_after_error_streak():
 x=VenueErrors(2);assert not x.failure("a").blocked;assert x.failure("a").blocked;assert not x.failure("b").blocked
