"""Data model for swiftcart line items."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str
    price: str          # price per unit, as a decimal string e.g. "19.99"
    category: str = "general"
