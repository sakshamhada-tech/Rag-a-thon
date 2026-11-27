"""In-memory stock table."""

_CATALOG = {"widget": 40, "gadget": 15, "sprocket": 0}


def stock_count(item):
    return _CATALOG.get(item, 0)
