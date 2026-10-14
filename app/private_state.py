from dataclasses import dataclass
@dataclass
class PrivateState:
    connected:bool=False
    last_event:float|None=None
    positions_trusted:bool=False
    orders_trusted:bool=False
    reason:str=""
    @property
    def ready(self):return self.connected and self.positions_trusted and self.orders_trusted and not self.reason
    def invalidate(self,reason):
        self.positions_trusted=False;self.orders_trusted=False;self.reason=reason
    def reconciled(self):
        self.positions_trusted=True;self.orders_trusted=True;self.reason=""
