"""Cart behaviour tests."""
from decimal import Decimal

from ledgerline import Cart, LineItem


def test_add_and_subtotal():
    cart = Cart()
    cart.add(LineItem("atlas", "12.50"), 2)
    cart.add(LineItem("dictionary", "40.00"), 1)
    assert cart.subtotal() == Decimal("65.00")


def test_remove_item():
    cart = Cart()
    cart.add(LineItem("atlas", "12.50"), 3)
    cart.remove("atlas", 1)
    assert cart.item_count() == 2
    assert cart.subtotal() == Decimal("25.00")


def test_empty_cart_total_is_zero():
    assert Cart().total() == Decimal("0.00")


def test_total_applies_volume_discount_even_cents():
    cart = Cart()
    cart.add(LineItem("notebook", "10.00"), 10)   # 5% of 100.00 = 5.00
    assert cart.total() == Decimal("95.00")


def test_item_count_sums_quantities():
    cart = Cart()
    cart.add(LineItem("atlas", "1.00"), 3)
    cart.add(LineItem("novel", "2.00"), 2)
    assert cart.item_count() == 5


def test_clear_empties_cart():
    cart = Cart()
    cart.add(LineItem("atlas", "9.99"), 4)
    cart.clear()
    assert cart.item_count() == 0
    assert cart.total() == Decimal("0.00")
