"""Money helpers for ledgerline."""
from decimal import Decimal
import decimal

CENT = Decimal("0.01")


def to_money(value) -> Decimal:
    """Coerce a value to a 2-decimal Decimal amount."""
    return Decimal(str(value)).quantize(CENT, rounding=decimal.ROUND_HALF_EVEN)
