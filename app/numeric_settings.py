"""Bounded operator number input. Never changes a setting until validation."""
import re
from decimal import Decimal


def parse(text, integer=False):
    value=(text or '').strip().replace(',', '.')
    if len(value)>32 or not re.fullmatch(r'[+]?(?:\d+(?:\.\d*)?|\.\d+)',value):
        raise ValueError('Пришли одно число, например 7,5')
    number=Decimal(value)
    if integer and number!=number.to_integral_value():
        raise ValueError('Нужно целое число')
    return int(number) if integer else float(number)
