"""Lọc riêng các dòng log LỖI (ERROR) từ file log — tiện xem nhanh qua đêm/
qua nhiều giờ không quan sát, không cần đọc hết cả file log dài. Chạy:
    python scripts/show_errors.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "eldorado_repricer.log"


def main() -> None:
    if not LOG_FILE.exists():
        print(f"Chưa có file log tại {LOG_FILE} — tool chưa chạy lần nào với bản code đã ghi log ra file.")
        return

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    error_lines = [line for line in lines if "[ERROR]" in line]
    if not error_lines:
        print(f"Không có dòng lỗi nào trong {len(lines)} dòng log — mọi thứ chạy suôn sẻ.")
        return

    print(f"Tìm thấy {len(error_lines)} dòng lỗi (trong tổng {len(lines)} dòng log):\n")
    for line in error_lines:
        print(line.rstrip())


if __name__ == "__main__":
    main()
