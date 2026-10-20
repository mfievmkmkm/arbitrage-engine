"""Regression: malformed fees for one coin must not disable the DEX spot universe.

The reader must preserve unknown fees as unknown, not infer a free transfer.
"""
from types import SimpleNamespace

from app.dex_spot_catalog import Catalog
from app.inventory_network_metadata import enrich


def test_bad_exchange_percentage_does_not_poison_all_currency_metadata():
    good_contract = "0x" + "1" * 40
    rows = {
        "BAD": {
            "code": "BAD",
            "networks": {
                "ERC20": {
                    "id": "ERC20",
                    "contract": "0x" + "2" * 40,
                    "percentage": False,
                    "info": {"withdrawPercentageFee": "not-a-number"},
                }
            },
        },
        "GOOD": {
            "code": "GOOD",
            "networks": {
                "ERC20": {
                    "id": "ERC20",
                    "contract": good_contract,
                    "info": {"withdrawPercentageFee": "0%"},
                }
            },
        },
    }
    result = enrich(rows)
    assert result["BAD"]["networks"]["ERC20"]["percentage"] is None
    assert result["BAD"]["networks"]["ERC20"]["_metadata_error"] == "WITHDRAW_PERCENTAGE_INVALID"
    assert result["GOOD"]["networks"]["ERC20"]["percentage"] is False
    assert "percentage" not in rows["GOOD"]["networks"]["ERC20"]

    client = SimpleNamespace(markets={
        "GOOD/USDT": {
            "spot": True, "active": True, "base": "GOOD", "quote": "USDT",
            "contract": False, "leveraged": False,
        }
    })
    assert Catalog.possibilities(client, result) == [
        ("GOOD/USDT", "GOOD", 1, good_contract)
    ]


def test_invalid_account_fee_remains_unknown_and_valid_zero_is_recognized():
    rows = {
        "X": {"code": "X", "networks": {
            "ERC20": {
                "id": "ERC20", "percentage": False,
                "info": {"withdrawPercentageFee": ""},
            },
            "BEP20": {
                "id": "BEP20", "percentage": True,
                "info": {"withdrawPercentageFee": "0"},
            },
        }}
    }
    fees = {"X": {"info": {"withdraw_percent_on_chains": {"ERC20": "unknown", "BEP20": "0%"}}}}
    result = enrich(rows, fees)
    eth = result["X"]["networks"]["ERC20"]
    bsc = result["X"]["networks"]["BEP20"]
    assert eth["percentage"] is None
    assert eth["_metadata_error"] == "ACCOUNT_WITHDRAW_PERCENTAGE_INVALID"
    assert bsc["percentage"] is False
