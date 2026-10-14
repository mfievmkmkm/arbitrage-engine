from dataclasses import dataclass
@dataclass
class OperatorState:
 strategy:str="futures_futures";page:int=0
 def select(self,strategy):self.strategy=strategy;self.page=0
