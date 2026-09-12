"""Unit test cho pricing.py (pure function, không I/O) — bản gốc (bản Python
cũ) không có test nào, đây là lần đầu các công thức này được test tự động."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricing import calculate_new_price, find_min_quantity


def test_no_competitor_no_floor_keeps_truncated_current_price():
    price = calculate_new_price(
        current_price=Decimal("12.3456"),
        min_competitor_price=None,
        discount_amount=Decimal("0"),
        price_min=None,
        price_max=None,
        undercut_from_competitor=True,
        round_decimals=2,
    )
    assert price == Decimal("12.34")


def test_no_competitor_falls_back_to_max_sheet():
    price = calculate_new_price(
        current_price=Decimal("5"),
        min_competitor_price=None,
        discount_amount=Decimal("0"),
        price_min=Decimal("1"),
        price_max=Decimal("9.99"),
        undercut_from_competitor=True,
        round_decimals=2,
    )
    assert price == Decimal("9.99")


def test_option_false_keeps_own_price_when_already_cheaper():
    price = calculate_new_price(
        current_price=Decimal("10"),
        min_competitor_price=Decimal("12"),
        discount_amount=Decimal("0.5"),
        price_min=Decimal("1"),
        price_max=Decimal("999"),
        undercut_from_competitor=False,
        round_decimals=2,
    )
    assert price == Decimal("10")


def test_option_true_undercuts_even_when_already_cheaper():
    price = calculate_new_price(
        current_price=Decimal("10"),
        min_competitor_price=Decimal("12"),
        discount_amount=Decimal("0.5"),
        price_min=Decimal("1"),
        price_max=Decimal("999"),
        undercut_from_competitor=True,
        round_decimals=2,
    )
    assert price == Decimal("11.5")


def test_undercuts_when_competitor_is_cheaper_regardless_of_option():
    price = calculate_new_price(
        current_price=Decimal("15"),
        min_competitor_price=Decimal("12"),
        discount_amount=Decimal("0.5"),
        price_min=Decimal("1"),
        price_max=Decimal("999"),
        undercut_from_competitor=False,
        round_decimals=2,
    )
    assert price == Decimal("11.5")


def test_never_undercuts_below_floor():
    price = calculate_new_price(
        current_price=Decimal("15"),
        min_competitor_price=Decimal("12"),
        discount_amount=Decimal("5"),
        price_min=Decimal("10"),
        price_max=Decimal("999"),
        undercut_from_competitor=True,
        round_decimals=2,
    )
    assert price == Decimal("10")


def test_always_truncates_down_never_rounds_up():
    """Bug thật đã gặp + sửa ở tool G2G repricer cùng công ty (xem
    g2g_repricing_tool_feature.md): làm tròn 'đến gần nhất' có thể vô tình
    đẩy giá LÊN cao hơn giá đối thủ vừa tính, phản tác dụng. calculate_new_price
    ở đây PHẢI luôn làm tròn xuống."""
    price_1_decimal = calculate_new_price(
        current_price=Decimal("120"),
        min_competitor_price=Decimal("113.98"),
        discount_amount=Decimal("0.00001"),
        price_min=Decimal("107.8"),
        price_max=Decimal("999"),
        undercut_from_competitor=True,
        round_decimals=1,
    )
    assert price_1_decimal == Decimal("113.9")

    price_2_decimals = calculate_new_price(
        current_price=Decimal("120"),
        min_competitor_price=Decimal("113.98"),
        discount_amount=Decimal("0.00001"),
        price_min=Decimal("107.8"),
        price_max=Decimal("999"),
        undercut_from_competitor=True,
        round_decimals=2,
    )
    assert price_2_decimals == Decimal("113.97")


def test_clamps_to_max_sheet():
    price = calculate_new_price(
        current_price=Decimal("5"),
        min_competitor_price=Decimal("2"),
        discount_amount=Decimal("0"),
        price_min=Decimal("1"),
        price_max=Decimal("1.5"),
        undercut_from_competitor=True,
        round_decimals=2,
    )
    assert price == Decimal("1.5")


def test_find_min_quantity_worked_examples():
    # Cùng công thức find_z đã dùng ở tool gốc, đối chiếu với ví dụ đã biết
    # đúng từ tool sibling (G2G repricer, cùng logic toán học).
    assert find_min_quantity(Decimal("1"), Decimal("0.085"), Decimal("10")) == 20
    assert find_min_quantity(Decimal("1"), Decimal("0.085"), Decimal("1")) == 12
