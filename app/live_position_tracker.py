class Tracker:
 def __init__(self):self.message_id=None;self.last_text=None
 def changed(self,text):
  if text==self.last_text:return False
  self.last_text=text;return True
 def bind(self,message_id):self.message_id=message_id
