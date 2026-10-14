class Router:
 def __init__(self):self.last={}
 def set(self,user,screen):self.last[user]=screen
 def get(self,user):return self.last.get(user,"home")
