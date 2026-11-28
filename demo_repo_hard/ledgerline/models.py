"""Data model for ledgerline line items."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    name: str
    price: str          # unit price as a decimal string, e.g. "19.97"
    category: str = "books"
