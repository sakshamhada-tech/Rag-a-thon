from meridian.catalog import available, price


def test_price_basic():
    assert price(2.5, 4) == 10.0


def test_price_zero_quantity():
    assert price(9.99, 0) == 0.0


def test_available_reports_stock():
    assert available("widget") == 40


def test_available_unknown_item():
    assert available("nonexistent") == 0
