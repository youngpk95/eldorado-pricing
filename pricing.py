"""Pure function tính giá/tồn kho/min-quantity — không I/O, dễ unit-test.
Dùng Decimal thay float (agent review khuyến nghị) để tránh sai số dấu phẩy
động khi làm tròn giá tới nhiều chữ số thập phân.

`calculate_new_price` port trực tiếp từ Eldorado._calculate_price_update
(bản gốc) — giữ NGUYÊN hướng LUÔN LÀM TRÒN XUỐNG (không phải round-to-nearest)
đúng như code gốc đã làm đúng.

Giá sàn/giá trần (PRICE_MIN/PRICE_MAX) giờ đọc THẲNG từ sheet (không còn suy
ra qua nhánh STOCK1/STOCK2 + tham chiếu chéo sheet khác như thiết kế ban đầu
— đã bỏ theo yêu cầu user 2026-09-13). Đã bỏ hẳn logic thời gian giao hàng
theo tồn kho (không dùng tới — xem eldorado_repricer_project.md nếu cần
thêm lại); số lượng đồng bộ lên Eldorado giờ luôn là giá trị cột STOCK, xử
lý trực tiếp trong product_pipeline.py."""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

MAX_DECIMAL = Decimal("1E999")  # tương đương "không giới hạn", thay cho sys.float_info.max của bản gốc


def _truncate(value: Decimal, decimals: int) -> Decimal:
    decimals = max(0, decimals)
    quantum = Decimal(1).scaleb(-decimals)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _max_decimals_allowed_by_eldorado(price: Decimal) -> int:
    """Giới hạn thật của Eldorado (khác với ROUND_DECIMALS người dùng tự
    chọn) — xác nhận từ lỗi 400 thật của API: 'Prices of 0.01 or higher allow
    up to 2 decimal places, prices below 0.01 allow up to 5'."""
    return 2 if price >= Decimal("0.01") else 5


def calculate_new_price(
    current_price: Decimal,
    min_competitor_price: Decimal | None,
    discount_amount: Decimal,
    price_min: Decimal | None,
    price_max: Decimal | None,
    undercut_from_competitor: bool,
    round_decimals: int,
) -> Decimal:
    """undercut_from_competitor tương ứng ALWAYS_UNDERCUT=='1' trên sheet:
    True = luôn trừ discount_amount từ giá đối thủ; False = khi giá mình
    đang RẺ hơn đối thủ thì giữ nguyên giá mình (không tự đẩy giá lên ngang
    đối thủ)."""
    max_sheet = price_max if price_max is not None else MAX_DECIMAL

    if min_competitor_price is not None and price_min is not None:
        if current_price < min_competitor_price:
            if undercut_from_competitor:
                candidate = _truncate(min_competitor_price - discount_amount, round_decimals)
            else:
                candidate = _truncate(current_price, round_decimals)
            new_price = max(candidate, price_min)
        else:
            candidate = _truncate(min_competitor_price - discount_amount, round_decimals)
            new_price = max(candidate, price_min)
    else:
        if price_max is not None:
            new_price = max_sheet
        elif price_min is not None and price_min != 0:
            new_price = price_min
        else:
            new_price = _truncate(current_price, round_decimals)

    final_price = min(new_price, max_sheet)
    # Chốt an toàn: PRICE_MIN/PRICE_MAX đọc thẳng từ sheet (hoặc sheet ngoài)
    # có thể mang nhiều số thập phân hơn API cho phép — nếu không cắt lại ở
    # đây, nhánh new_price = price_min/max_sheet phía trên có thể gửi thẳng
    # giá đó lên Eldorado và bị từ chối (lỗi 400 "too many decimal places").
    return _truncate(final_price, _max_decimals_allowed_by_eldorado(final_price))


def find_min_quantity(total_order_min: Decimal, price: Decimal, round_coef: Decimal) -> int:
    """Port từ Gsphelper.find_z — minQuantity nhỏ nhất sao cho tổng giá trị
    đơn hàng tối thiểu đạt total_order_min VÀ minQuantity là bội số của
    round_coef."""
    safe_price = price if price != 0 else Decimal("0.01")
    x = int(total_order_min / safe_price)
    coef = round_coef if round_coef != 0 else Decimal(1)
    z = 1
    while (x + z) % coef != 0:
        z += 1
    return z + x
