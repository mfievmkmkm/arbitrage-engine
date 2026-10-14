import os
SUPPORTED=("binance","bybit","okx","bitget","gateio","mexc","bingx")
def credentials(name):
 prefix=name.upper()
 key=os.getenv(prefix+"_API_KEY","").strip()
 secret=os.getenv(prefix+"_API_SECRET","").strip()
 password=os.getenv(prefix+"_API_PASSWORD","").strip()
 return {"apiKey":key,"secret":secret,"password":password}
def configured():
 return [x for x in SUPPORTED if credentials(x)["apiKey"] and credentials(x)["secret"]]
