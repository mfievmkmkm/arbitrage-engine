KEYS=("api_key","apikey","secret","password","token","private_key","seed")
def redact(obj):
 if isinstance(obj,dict):return {k:("***" if any(x in k.lower() for x in KEYS) else redact(v)) for k,v in obj.items()}
 if isinstance(obj,list):return [redact(x) for x in obj]
 return obj
