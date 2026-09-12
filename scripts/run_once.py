"""Chạy ĐÚNG 1 chu kỳ (đọc sheet, xử lý mọi sản phẩm ENABLED) rồi thoát —
tiện để test nhanh sau khi sửa sheet/code, không cần chờ vòng lặp vô hạn của
main.py. Vẫn tôn trọng DRY_RUN trong .env như bình thường."""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging  # noqa: E402

import config  # noqa: E402
from eldo_auth import EldoradoAuth  # noqa: E402
from eldorado_api import EldoradoClient  # noqa: E402
from main import run_one_cycle  # noqa: E402
from sheets_client import SheetsClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main() -> None:
    logging.info("Chạy 1 chu kỳ — DRY_RUN=%s", config.DRY_RUN)
    sheets = SheetsClient()
    auth = EldoradoAuth()
    client = EldoradoClient(get_cookie=auth.get_cookie)
    semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)
    try:
        await run_one_cycle(sheets, client, semaphore)
    finally:
        await client.aclose()
    logging.info("Xong. Xem kết quả trong cột Status/Updated At/New Link trên sheet.")


if __name__ == "__main__":
    asyncio.run(main())
