import pytest
from shop.pricing import apply_discount

def test_apply_discount_normal():
    """Test normal discount application."""
    assert apply_discount(100, 10) == 90.00

def test_apply_discount_zero_discount():
    """Test applying zero discount."""
    assert apply_discount(100, 0) == 100.00

def test_apply_discount_full_discount():
    """Test applying full discount."""
    assert apply_discount(100, 100) == 0.00

def test_apply_discount_negative_price():
    """Test applying discount with negative price."""
    with pytest.raises(ValueError, match="price must not be negative"):
        apply_discount(-100, 10)

def test_apply_discount_negative_discount():
    """Test applying discount with negative percentage."""
    with pytest.raises(ValueError, match="percent must be between 0 and 100"):
        apply_discount(100, -10)

def test_apply_discount_high_discount():
    """Test applying discount with high percentage."""
    with pytest.raises(ValueError, match="percent must be between 0 and 100"):
        apply_discount(100, 110)
