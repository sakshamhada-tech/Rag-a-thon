import pytest

from skylark.pricing import apply_discount, format_total


def test_apply_discount_basic():
    assert apply_discount(100, 0.1) == pytest.approx(90.0)


def test_apply_discount_zero():
    assert apply_discount(50, 0) == pytest.approx(50.0)


def test_apply_discount_validation():
    with pytest.raises(ValueError):
        apply_discount(100, 1.5)


def test_format_total():
    assert format_total(123.4) == "$123.40"
