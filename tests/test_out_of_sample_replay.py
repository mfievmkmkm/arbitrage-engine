from app.out_of_sample_replay import evaluate
def test_oos_replay_reports_train_and_validation():
 x=evaluate([1]*10);assert x["train"]["trades"]==7 and x["validation"]["trades"]==3 and x["valid"]
