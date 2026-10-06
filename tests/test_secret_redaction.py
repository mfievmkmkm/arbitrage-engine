from app.secret_redaction import redact
def test_exports_redact_secrets():
 x=redact({"api_key":"abc","nested":{"token":"x","value":1}});assert x["api_key"]=="***" and x["nested"]["token"]=="***" and x["nested"]["value"]==1
