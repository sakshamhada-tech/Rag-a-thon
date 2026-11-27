# Swiftcart

A small shopping-cart / bulk-discount calculator written in pure Python
(3.11+), standard library only.

## Layout

- `swiftcart/models.py` — `Item` data model
- `swiftcart/money.py` — money coercion helpers
- `swiftcart/discounts.py` — bulk discount rules
- `swiftcart/cart.py` — `Cart` implementation
- `tests/` — pytest suite

## Usage

```python
from swiftcart import Cart, Item

cart = Cart()
cart.add(Item("apple", "1.50"), 3)
print(cart.total())
```

## Run the tests

```bash
python -m pytest
```
