"""Bulk-purchase discount rules for swiftcart."""
from decimal import Decimal

from .money import to_money

_TWO_DECIMAL_ZERO = Decimal("0.00")


def bulk_discount_rate(quantity: int) -> Decimal:
    """Discount rate for a bulk purchase of `quantity` units."""
    if quantity >= 25:
        return Decimal("0.10")
    if quantity >= 10:
        return Decimal("0.05")
    return _TWO_DECIMAL_ZERO


def discount_amount(subtotal, quantity: int) -> Decimal:
    """Amount to subtract from `subtotal` for the given bulk quantity."""
    return to_money(to_money(subtotal) * bulk_discount_rate(quantity))


def discounted_subtotal(subtotal, quantity: int) -> Decimal:
    """Subtotal after applying the bulk discount for `quantity` units."""
    return to_money(to_money(subtotal) - discount_amount(subtotal, quantity))
