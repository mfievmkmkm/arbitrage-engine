"""Read-only adapter for fields CCXT leaves in exchange-native info.

Sources: bybit-exchange.github.io/docs/v5/asset/coin-info and
www.gate.com/docs/developers/apiv4/en/#query-withdrawal-status .
Missing minima/fees stay missing. Nonzero percentage fees are unsupported.
"""
import copy
from .strategy_math import D


def _percentage(value):
    """Read exchange-native fee percentages without turning unknown into zero."""
    if isinstance(value, str):
        value = value.strip()
        if value.endswith("%"):
            value = value[:-1].strip()
    return D(value) != 0


def _set_network_percentage(network, value, source):
    try:
        network["percentage"] = _percentage(value)
    except (ValueError, TypeError, ArithmeticError):
        # One malformed coin must not stop discovery of every other coin.
        # Unknown never authorizes a transfer: downstream gates require False.
        network["percentage"] = None
        network["_metadata_error"] = source + "_INVALID"



def enrich(currencies,fees=None):
    rows=copy.deepcopy(currencies)
    for code,currency in rows.items():
        fee_row=(fees or {}).get(code,{})
        raw_fee=fee_row.get('info') or {}
        for key,network in (currency.get('networks') or {}).items():
            raw=network.get('info') or {}
            contract=raw.get('contractAddress') or raw.get('addr')
            if contract:
                network['contract']=contract
            if raw.get('withdraw_delayed') is True:
                network['withdraw']=False
            if 'withdrawPercentageFee' in raw:
                _set_network_percentage(network, raw['withdrawPercentageFee'], 'WITHDRAW_PERCENTAGE')
            unified_fee=(fee_row.get('networks') or {}).get(key,{})
            withdraw=unified_fee.get('withdraw') or {}
            if withdraw.get('fee') is not None:
                network['fee']=withdraw['fee']
            if withdraw.get('percentage') is not None:
                network['percentage']=network.get('percentage') is True or withdraw['percentage'] is True
            deposit=unified_fee.get('deposit') or {}
            if deposit.get('fee') is not None:
                network['deposit_fee']=deposit['fee']
                network['deposit_fee_percentage']=deposit.get('percentage')
            chain=network.get('id')
            percentages=raw_fee.get('withdraw_percent_on_chains') or {}
            percentage=percentages.get(chain,raw_fee.get('withdraw_percent'))
            if percentage is not None:
                _set_network_percentage(network, percentage, 'ACCOUNT_WITHDRAW_PERCENTAGE')
            fixed=(raw_fee.get('withdraw_fix_on_chains') or {}).get(chain)
            if fixed is not None and network.get('percentage') is False:
                network['fee']=fixed
            minimum=raw_fee.get('withdraw_amount_mini')
            if minimum is not None:
                network.setdefault('limits',{}).setdefault('withdraw',{})['min']=minimum
    return rows
