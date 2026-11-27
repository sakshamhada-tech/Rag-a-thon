"""Cart behaviour tests."""
from decimal import Decimal

from swiftcart import Cart, Item


def test_add_and_subtotal():
    cart = Cart()
    cart.add(Item("apple", "1.50"), 2)
    cart.add(Item("bread", "3.25"), 1)
    assert cart.subtotal() == Decimal("6.25")


def test_remove_item():
    cart = Cart()
    cart.add(Item("apple", "1.50"), 3)
    cart.remove("apple", 1)
    assert cart.item_count() == 2
    assert cart.subtotal() == Decimal("3.00")


def test_remove_missing_item_is_noop():
    cart = Cart()
    cart.remove("nonexistent")
    assert cart.total() == Decimal("0.00")


def test_empty_cart_total_is_zero():
    assert Cart().total() == Decimal("0.00")


def test_clear_empties_cart():
    cart = Cart()
    cart.add(Item("apple", "2.00"), 5)
    cart.clear()
    assert cart.item_count() == 0
    assert cart.total() == Decimal("0.00")


def test_item_count_sums_quantities():
    cart = Cart()
    cart.add(Item("apple", "1.00"), 3)
    cart.add(Item("milk", "2.00"), 2)
    assert cart.item_count() == 5


def test_total_applies_bulk_discount_even_cents():
    cart = Cart()
    cart.add(Item("notebook", "10.00"), 10)   # 5% of 100.00 = 5.00
    assert cart.total() == Decimal("95.00")


def test_add_rejects_non_positive_quantity():
    cart = Cart()
    try:
        cart.add(Item("apple", "1.00"), 0)
        assert False, "expected ValueError"
    except ValueError:
        pass
