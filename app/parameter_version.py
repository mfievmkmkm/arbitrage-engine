import hashlib,json
def version(params):return hashlib.sha256(json.dumps(params,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:12]
