"""Catalogue price and availability helpers."""
from .inventory import stock_level


def price(unit_price, quantity):
    return round(unit_price * quantity, 2)


def available(item):
    return stock_level(item)
