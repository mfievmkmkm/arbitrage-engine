import re

KEYS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "private_key",
    "privatekey",
    "seed",
    "raw_transaction",
    "signed_transaction",
    "signed_raw",
    "raw_signature",
    "rawtransaction",
    "signedtransaction",
    "signedraw",
    "rawsignature",
    "authorization",
)
PUBLIC_TOKEN_FIELDS = {
    "sell_token",
    "buy_token",
    "entry_sell_token",
    "entry_buy_token",
    "quote_token",
    "token_deltas",
    "tokens",
    "tokenMetadata",
    "token_contract",
}


def public_token_value(key, value):
    if key not in PUBLIC_TOKEN_FIELDS:
        return False

    def contract(x):
        return isinstance(x, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", x) is not None

    if key == "tokens":
        return (isinstance(value, list) and all(contract(x) for x in value)) or (
            isinstance(value, dict)
            and all(contract(x) and isinstance(y, dict) for x, y in value.items())
        )
    if key == "token_deltas":
        return isinstance(value, dict) and all(
            contract(x)
            and isinstance(y, str)
            and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", y)
            for x, y in value.items()
        )
    if key == "tokenMetadata":
        return (
            isinstance(value, dict)
            and set(value) <= {"buyToken", "sellToken"}
            and all(isinstance(v, dict) for v in value.values())
        )
    return contract(value)


def redact(obj):
    if isinstance(obj, dict):
        return {
            k: (
                "***"
                if not public_token_value(k, v)
                and (k.lower() == "raw" or any(x in k.lower() for x in KEYS))
                else redact(v)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(x) for x in obj]
    return obj
