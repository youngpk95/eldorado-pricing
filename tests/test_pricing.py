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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
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
        offer_type="Item",
    )
    assert price == Decimal("1.5")


def test_price_max_with_too_many_decimals_gets_truncated_to_eldorado_limit():
    """Bug thật gặp 2026-09-15: Price Max đọc từ sheet ngoài trả về
    15.5184 (4 số thập phân) và bị gửi thẳng lên Eldorado, API từ chối
    (400 'too many decimal places' — chỉ cho tối đa 2 số khi giá >= 0.01)."""
    price = calculate_new_price(
        current_price=Decimal("100"),
        min_competitor_price=Decimal("35.9"),
        discount_amount=Decimal("0.01"),
        price_min=Decimal("1"),
        price_max=Decimal("15.5184"),
        undercut_from_competitor=True,
        round_decimals=2,
        offer_type="Item",
    )
    assert price == Decimal("15.51")


def test_currency_keeps_old_decimal_logic_not_truncated_to_two():
    """Currency/TopUp/GiftCard KHÔNG áp rule 2-vs-5-theo-ngưỡng-0.01 (rule đó
    chỉ đúng cho Item, xác nhận từ người dùng 2026-09-16) — giữ nguyên
    ROUND_DECIMALS của sheet, tối đa 6 số thập phân."""
    price = calculate_new_price(
        current_price=Decimal("100"),
        min_competitor_price=Decimal("35.9"),
        discount_amount=Decimal("0.01"),
        price_min=Decimal("1"),
        price_max=Decimal("15.5184"),
        undercut_from_competitor=True,
        round_decimals=6,
        offer_type="Currency",
    )
    assert price == Decimal("15.5184")


def test_currency_still_capped_at_six_decimals_max():
    """Ngay cả Currency cũng phải chốt an toàn tối đa 6 số thập phân (hành vi
    CŨ trước commit d8c4cc7) để không gửi giá >6 số thập phân lên Eldorado."""
    price = calculate_new_price(
        current_price=Decimal("100"),
        min_competitor_price=None,
        discount_amount=Decimal("0"),
        price_min=None,
        price_max=Decimal("0.123456789"),
        undercut_from_competitor=True,
        round_decimals=6,
        offer_type="TopUp",
    )
    assert price == Decimal("0.123456")


def test_price_below_one_cent_allows_up_to_5_decimals():
    price = calculate_new_price(
        current_price=Decimal("1"),
        min_competitor_price=None,
        discount_amount=Decimal("0"),
        price_min=None,
        price_max=Decimal("0.0056789"),
        undercut_from_competitor=True,
        round_decimals=2,
        offer_type="Item",
    )
    assert price == Decimal("0.00567")


def test_find_min_quantity_worked_examples():
    # Cùng công thức find_z đã dùng ở tool gốc, đối chiếu với ví dụ đã biết
    # đúng từ tool sibling (G2G repricer, cùng logic toán học).
    assert find_min_quantity(Decimal("1"), Decimal("0.085"), Decimal("10")) == 20
    assert find_min_quantity(Decimal("1"), Decimal("0.085"), Decimal("1")) == 12
