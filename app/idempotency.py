class IdempotencyGuard:
 def __init__(self):self.active=set();self.completed=set()
 def begin(self,key):
  if key in self.active or key in self.completed:return False
  self.active.add(key);return True
 def finish(self,key):
  self.active.discard(key);self.completed.add(key)
 def fail(self,key):self.active.discard(key)
