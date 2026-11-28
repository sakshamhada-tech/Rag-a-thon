"""Meridian — tiny inventory-backed catalogue pricer.

Standard library only at runtime. Tests use pytest.
"""
from .catalog import price, available

__all__ = ["price", "available"]
__version__ = "1.0.0"
