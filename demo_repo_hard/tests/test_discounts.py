"""Volume discount tests."""
from decimal import Decimal

from ledgerline import volume_rate, discount_amount, discounted_subtotal


def test_no_discount_below_ten_items():
    assert volume_rate(9) == Decimal("0.00")
    assert discount_amount("90.00", 9) == Decimal("0.00")


def test_rate_applies_at_ten_items():
    assert volume_rate(10) == Decimal("0.05")
    assert volume_rate(24) == Decimal("0.05")


def test_rate_applies_at_twentyfive_items():
    assert volume_rate(25) == Decimal("0.10")
    assert discount_amount("250.00", 25) == Decimal("25.00")


def test_five_percent_rounds_half_up():
    # 5% of 199.70 = 9.985
    assert discount_amount("199.70", 10) == Decimal("9.99")


def test_five_percent_rounds_half_up_small():
    # 5% of 59.70 = 2.985
    assert discount_amount("59.70", 10) == Decimal("2.99")


def test_ten_percent_rounds_half_up():
    # 10% of 499.65 = 49.965
    assert discount_amount("499.65", 25) == Decimal("49.97")


def test_discounted_subtotal_exact_cents():
    # 10% of 200.00 = 20.00
    assert discounted_subtotal("200.00", 25) == Decimal("180.00")


def test_discount_below_threshold_is_zero():
    assert discount_amount("199.70", 9) == Decimal("0.00")
