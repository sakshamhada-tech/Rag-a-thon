"""Shopping cart implementation for swiftcart."""
from decimal import Decimal

from .models import Item
from .money import to_money
from .discounts import discount_amount, discounted_subtotal


class Cart:
    """A simple in-memory cart with bulk discounts."""

    def __init__(self):
        self._lines: dict[str, list] = {}

    def add(self, item: Item, quantity: int = 1) -> None:
        if quantity < 1:
            raise ValueError("quantity must be >= 1")
        line = self._lines.setdefault(item.name, [item, 0])
        line[1] += quantity

    def remove(self, name: str, quantity: int | None = None) -> None:
        if name not in self._lines:
            return
        if quantity is None or quantity >= self._lines[name][1]:
            del self._lines[name]
        else:
            self._lines[name][1] -= quantity

    def clear(self) -> None:
        self._lines.clear()

    def item_count(self) -> int:
        return sum(qty for _, qty in self._lines.values())

    def subtotal(self) -> Decimal:
        total = sum((Decimal(item.price) * qty for item, qty in self._lines.values()),
                    Decimal("0"))
        return to_money(total)

    def discount(self) -> Decimal:
        return discount_amount(self.subtotal(), self.item_count())

    def total(self) -> Decimal:
        return discounted_subtotal(self.subtotal(), self.item_count())

    def __len__(self) -> int:
        return len(self._lines)
