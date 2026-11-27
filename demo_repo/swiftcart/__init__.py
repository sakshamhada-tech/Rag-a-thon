"""Swiftcart — a small shopping-cart and discount calculator.

Standard library only at runtime. Tests use pytest.
"""
from .models import Item
from .cart import Cart
from .discounts import bulk_discount_rate, discounted_subtotal, discount_amount

__all__ = ["Item", "Cart", "bulk_discount_rate", "discounted_subtotal", "discount_amount"]
__version__ = "1.0.0"
