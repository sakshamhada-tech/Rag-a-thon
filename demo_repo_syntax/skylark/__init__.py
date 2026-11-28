"""Skylark — tiny order pricing helpers.

Standard library only at runtime. Tests use pytest.
"""
from .pricing import apply_discount, format_total

__all__ = ["apply_discount", "format_total"]
__version__ = "1.0.0"
