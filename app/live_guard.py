from dataclasses import dataclass
@dataclass
class LiveGuard:
    enabled:bool=False
    private_streams_ready:bool=False
    position_state_trusted:bool=False
    kill_reason:str=""
    def can_trade(self):
        return self.enabled and self.private_streams_ready and self.position_state_trusted and not self.kill_reason
    def kill(self,reason):
        self.kill_reason=reason;self.enabled=False
    def arm(self):
        if not self.private_streams_ready or not self.position_state_trusted:return False
        self.enabled=True;self.kill_reason="";return True
