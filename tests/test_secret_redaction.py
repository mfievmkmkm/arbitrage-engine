from app.secret_redaction import redact


def test_exports_redact_secrets():
    x = redact({"api_key": "abc", "nested": {"token": "x", "value": 1}})
    assert (
        x["api_key"] == "***"
        and x["nested"]["token"] == "***"
        and x["nested"]["value"] == 1
    )


def test_wallet_export_preserves_public_cashflow_but_redacts_signing_capabilities():
    address = "0x" + "a" * 40
    x = redact(
        {
            "sell_token": address,
            "token_deltas": {address: "-4"},
            "signed_raw": "raw",
            "raw_transaction": "raw",
            "BOT_TOKEN": "secret",
        }
    )
    assert x["sell_token"] == address and x["token_deltas"] == {address: "-4"}
    assert x["signed_raw"] == x["raw_transaction"] == x["BOT_TOKEN"] == "***"


def test_public_token_field_names_cannot_hide_credentials():
    x = redact(
        {
            "tokens": ["credential"],
            "sell_token": "secret",
            "token_deltas": {"not-an-address": "key"},
            "rawTransaction": "raw",
        }
    )
    assert all(v == "***" for v in x.values())
