"""Volume discount rules for ledgerline."""
from decimal import Decimal

from .money import to_money

_ZERO = Decimal("0.00")


def volume_rate(quantity: int) -> Decimal:
    """Discount rate for a bulk purchase of `quantity` units."""
    if quantity > 25:
        return Decimal("0.10")
    if quantity >= 10:
        return Decimal("0.05")
    return _ZERO


def discount_amount(subtotal, quantity: int) -> Decimal:
    """Amount to subtract from `subtotal` for the given bulk quantity."""
    return to_money(to_money(subtotal) * volume_rate(quantity))


def discounted_subtotal(subtotal, quantity: int) -> Decimal:
    """Subtotal after applying the volume discount for `quantity` units."""
    return to_money(to_money(subtotal) - discount_amount(subtotal, quantity))
