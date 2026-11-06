"""A small Python project with a known cross-file bug, shared by the fix-session tests.

The symptom is in shop/orders.py (average_order_value crashes on an empty order list), but the
root cause is shop/pricing.py:average, which divides by len(values) without checking for zero.
tests/test_orders.py::test_empty_orders fails before the fix; the other tests pass.
"""

from pathlib import Path

PRICING = (
    "def average(values):\n"
    "    total = sum(values)\n"
    "    return total / len(values)\n"
    "\n"
    "\n"
    "def apply_discount(price, percent):\n"
    "    return price - price * percent / 100\n"
)

ORDERS = (
    "from shop.pricing import average\n"
    "\n"
    "\n"
    "def average_order_value(orders):\n"
    "    totals = [order[\"total\"] for order in orders]\n"
    "    return average(totals)\n"
)

TEST_PRICING = (
    "from shop.pricing import apply_discount, average\n"
    "\n"
    "\n"
    "def test_average():\n"
    "    assert average([2, 4]) == 3\n"
    "\n"
    "\n"
    "def test_discount():\n"
    "    assert apply_discount(200, 10) == 180\n"
)

TEST_ORDERS = (
    "from shop.orders import average_order_value\n"
    "\n"
    "\n"
    "def test_average_order_value():\n"
    "    assert average_order_value([{\"total\": 10}, {\"total\": 30}]) == 20\n"
    "\n"
    "\n"
    "def test_empty_orders():\n"
    "    assert average_order_value([]) == 0.0\n"
)

FIXED_PRICING = PRICING.replace(
    "    total = sum(values)\n", "    if not values:\n        return 0.0\n    total = sum(values)\n"
)

FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": PRICING,
    "shop/orders.py": ORDERS,
    "tests/__init__.py": "",
    "tests/test_pricing.py": TEST_PRICING,
    "tests/test_orders.py": TEST_ORDERS,
    "pytest.ini": "[pytest]\ntestpaths = tests\n",
    ".env": "PAYMENT_API_KEY=live-secret-value\n",
    "README.md": "# Shop\n",
}


def make_shop_repo(root: Path) -> Path:
    root = root.resolve()
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root
