from app.replay_split import split
def test_replay_has_out_of_sample_partition():
 a,b=split(list(range(10)),.7);assert len(a)==7 and len(b)==3
