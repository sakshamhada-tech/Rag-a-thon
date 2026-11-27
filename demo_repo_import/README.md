# Meridian

A tiny inventory-backed catalogue pricer.
Standard library only at runtime. Tests use pytest.

## Layout

- `meridian/inventory.py` — `stock_count` stock lookup
- `meridian/catalog.py` — `price`, `available` helpers
- `tests/` — pytest suite

## Run the tests

```bash
python -m pytest
```
