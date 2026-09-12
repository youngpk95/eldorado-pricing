"""Entrypoint — vòng lặp vô hạn: đọc sheet, xử lý mọi sản phẩm SONG SONG (giới
hạn qua Semaphore), nghỉ, lặp lại. Thay cho ThreadPoolExecutor + nhiều Service
Account xoay vòng của bản gốc (main.py cũ)."""
from __future__ import annotations

import asyncio
import io
import logging
import logging.handlers
import sys
from pathlib import Path

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
from product_pipeline import RowConfig, process_product
from sheets_client import SheetsClient

# Ghi log ra CẢ file lẫn console — đóng terminal/tắt máy không còn làm mất
# lịch sử log, mai quay lại vẫn xem được lỗi/bug đã xảy ra qua đêm. Xoay
# vòng file khi quá 5MB, giữ tối đa 5 file cũ (đủ nhiều ngày chạy 24/7, tự
# dọn để không phình ổ đĩa vô hạn).
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "eldorado_repricer.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)


def _compute_loop_delay(rows: list[ProductRow]) -> float:
    """Relax (giây nghỉ sau khi xong CẢ vòng) là cấu hình chung cho toàn bộ
    vòng chạy, đọc từ cột RELAX_SECONDS — nếu nhiều sản phẩm đang bật có số
    khác nhau, lấy số LỚN NHẤT (an toàn hơn, không sản phẩm nào bị chạy dày
    hơn ý muốn). Để trống hết thì dùng mặc định config.LOOP_DELAY_SECONDS."""
    relax_values = [
        cfg.relax_seconds
        for cfg in (RowConfig(r) for r in rows)
        if cfg.enabled and cfg.relax_seconds is not None
    ]
    return max(relax_values) if relax_values else config.LOOP_DELAY_SECONDS


async def run_one_cycle(sheets: SheetsClient, client: EldoradoClient, semaphore: asyncio.Semaphore) -> float:
    raw_rows = await asyncio.to_thread(sheets.read_config_rows)
    rows = [ProductRow(index=i, raw=raw) for i, raw in enumerate(raw_rows)]
    logger.info("Có %d sản phẩm trong sheet.", len(rows))

    async def guarded(row: ProductRow) -> None:
        async with semaphore:
            await process_product(row, sheets, client)
            if config.PRODUCT_DELAY_SECONDS > 0:
                await asyncio.sleep(config.PRODUCT_DELAY_SECONDS)

    await asyncio.gather(*(guarded(row) for row in rows))
    return _compute_loop_delay(rows)


async def main() -> None:
    logger.info("Khởi động Eldorado Repricer — DRY_RUN=%s", config.DRY_RUN)
    sheets = SheetsClient()
    auth = EldoradoAuth()
    client = EldoradoClient(get_cookie=auth.get_cookie)
    semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)

    try:
        while True:
            started = asyncio.get_event_loop().time()
            loop_delay = config.LOOP_DELAY_SECONDS
            try:
                loop_delay = await run_one_cycle(sheets, client, semaphore)
            except Exception:
                logger.exception("Lỗi ở 1 chu kỳ chạy — bỏ qua, thử lại chu kỳ sau.")
            elapsed = asyncio.get_event_loop().time() - started
            logger.info("Hoàn tất 1 chu kỳ trong %.1fs. Nghỉ %ss...", elapsed, loop_delay)
            await asyncio.sleep(loop_delay)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
