"""Discount and formatting helpers."""


def apply_discount(total, discount):
    if discount < 0 or discount > 1:
        raise ValueError("discount must be between 0 and 1")
    return total * (1 - discount)


def format_total(total):
    return f"${total:.2f}
