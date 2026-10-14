from .replay_split import split
from .spot_future_replay import metrics
def evaluate(nets,train_ratio=.7):
 train,test=split(nets,train_ratio);return {"train":metrics(train),"validation":metrics(test),"valid":bool(test and metrics(test) and metrics(test)["net"]>0)}
