from app.live_position_tracker import Tracker
def test_tracker_only_edits_changed_text():
 x=Tracker();assert x.changed("a");assert not x.changed("a");assert x.changed("b")
