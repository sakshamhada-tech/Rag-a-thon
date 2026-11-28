# Ledgerline

A tiny invoice/discount calculator for a fictional bookshop bulk-order desk.
Pure Python (3.11+), standard library only.

## Layout

- `ledgerline/models.py` — `LineItem`
- `ledgerline/money.py` — monetary helpers
- `ledgerline/discounts.py` — volume discount rules
- `ledgerline/cart.py` — `Cart` with bulk discounts
- `tests/` — pytest suite

## Usage

```python
from ledgerline import Cart, LineItem

cart = Cart()
cart.add(LineItem("The Great Gatsby", "19.97"), 10)
print(cart.total())
```

## Run the tests

```bash
python -m pytest
```
