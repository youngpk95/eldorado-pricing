"""Entrypoint — vòng lặp vô hạn: đọc sheet, xử lý mọi sản phẩm SONG SONG (giới
hạn qua Semaphore), nghỉ, lặp lại. Thay cho ThreadPoolExecutor + nhiều Service
Account xoay vòng của bản gốc (main.py cũ)."""
from __future__ import annotations

import asyncio
import io
import logging
import sys

# Ép UTF-8 cho stdout/stderr — console Windows mặc định (code page cp1252)
# có thể crash (UnicodeEncodeError) hoặc in lỗi khi log chứa tiếng Việt có
# dấu. Cùng vấn đề + cùng cách sửa như main.py của bản gốc.
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import config
from eldo_auth import EldoradoAuth
from eldorado_api import EldoradoClient
from models import ProductRow
from product_pipeline import process_product
from sheets_client import SheetsClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def run_one_cycle(sheets: SheetsClient, client: EldoradoClient, semaphore: asyncio.Semaphore) -> None:
    raw_rows = await asyncio.to_thread(sheets.read_config_rows)
    rows = [ProductRow(index=i, raw=raw) for i, raw in enumerate(raw_rows)]
    logger.info("Có %d sản phẩm trong sheet.", len(rows))

    async def guarded(row: ProductRow) -> None:
        async with semaphore:
            await process_product(row, sheets, client)
            if config.PRODUCT_DELAY_SECONDS > 0:
                await asyncio.sleep(config.PRODUCT_DELAY_SECONDS)

    await asyncio.gather(*(guarded(row) for row in rows))


async def main() -> None:
    logger.info("Khởi động Eldorado Repricer — DRY_RUN=%s", config.DRY_RUN)
    sheets = SheetsClient()
    auth = EldoradoAuth()
    client = EldoradoClient(get_cookie=auth.get_cookie)
    semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)

    try:
        while True:
            started = asyncio.get_event_loop().time()
            try:
                await run_one_cycle(sheets, client, semaphore)
            except Exception:
                logger.exception("Lỗi ở 1 chu kỳ chạy — bỏ qua, thử lại chu kỳ sau.")
            elapsed = asyncio.get_event_loop().time() - started
            logger.info("Hoàn tất 1 chu kỳ trong %.1fs. Nghỉ %ss...", elapsed, config.LOOP_DELAY_SECONDS)
            await asyncio.sleep(config.LOOP_DELAY_SECONDS)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
