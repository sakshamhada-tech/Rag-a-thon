"""Bulk discount tests."""
from decimal import Decimal

from swiftcart import discount_amount, discounted_subtotal, bulk_discount_rate


def test_no_discount_below_ten_items():
    assert bulk_discount_rate(9) == Decimal("0.00")
    assert discount_amount("90.00", 9) == Decimal("0.00")


def test_discount_rate_tiers():
    assert bulk_discount_rate(10) == Decimal("0.05")
    assert bulk_discount_rate(24) == Decimal("0.05")
    assert bulk_discount_rate(25) == Decimal("0.10")


def test_discount_at_ten_items_boundary():
    # 5% of 19.99 = 0.9995
    assert discount_amount("19.99", 10) == Decimal("1.00")


def test_discount_at_twentyfive_items_boundary():
    # 10% of 499.75 = 49.975
    assert discount_amount("499.75", 25) == Decimal("49.98")


def test_discount_rounds_half_up_on_odd_cents():
    # 5% of 59.97 = 2.9985
    assert discount_amount("59.97", 10) == Decimal("3.00")


def test_discounted_subtotal_exact_cents():
    # 10% of 200.00 = 20.00
    assert discounted_subtotal("200.00", 25) == Decimal("180.00")
