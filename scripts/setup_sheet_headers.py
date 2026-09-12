"""Script thiết lập 1 LẦN cho 1 tab config sản phẩm: xoá sạch dữ liệu cũ +
ghi nhãn hiển thị (tiếng Việt, ngắn gọn) kèm chú thích (note, hiện khi rê
chuột) cho từng cột — xem sheet_schema.py cho định nghĩa đầy đủ. Chạy tay khi
tạo tab mới hoặc muốn đồng bộ lại nhãn/chú thích — KHÔNG chạy trong vòng lặp
chính (main.py)."""
from __future__ import annotations

import io
import sys
from pathlib import Path

# Ép UTF-8 cho stdout — console Windows mặc định (cp1252) có thể crash khi in
# tiếng Việt có dấu (xác nhận thật khi test script này).
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from sheet_schema import COLUMNS, INTERNAL_KEYS  # noqa: E402
from sheets_client import SheetsClient  # noqa: E402


def main() -> None:
    sc = SheetsClient()
    clear_range = f"{config.CONFIG_RANGE}!A1:BZ1000"
    print(f"Xoá dữ liệu cũ trong '{clear_range}'...")
    sc.clear_range(clear_range)
    print(f"Ghi {len(COLUMNS)} cột header (nhãn + chú thích) vào dòng 1...")
    sc.write_header_row_with_notes()
    checkbox_columns = ["ENABLED", "ALWAYS_UNDERCUT", "ALLOW_RECREATE_ON_RATE_LIMIT"]
    checkbox_indices = [INTERNAL_KEYS.index(key) for key in checkbox_columns]
    print(f"Đặt checkbox cho {len(checkbox_columns)} cột ({', '.join(checkbox_columns)}), dòng 2-1000...")
    sc.set_checkbox_columns(checkbox_indices)
    print("Xong. Rê chuột vào từng ô header trên Google Sheets để xem chú thích.")


if __name__ == "__main__":
    main()
