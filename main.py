"""Entrypoint — vòng lặp vô hạn: đọc sheet, xử lý mọi sản phẩm SONG SONG (giới
hạn qua Semaphore), nghỉ, lặp lại. Thay cho ThreadPoolExecutor + nhiều Service
Account xoay vòng của bản gốc (main.py cũ). Sau mỗi chu kỳ (điểm an toàn,
không có task dở dang) còn tự kiểm tra + tự cập nhật code từ GitHub nếu tới
kỳ hạn — xem updater.py."""
from __future__ import annotations

import asyncio
import io
import logging
import logging.handlers
import os
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
import updater
from eldo_auth import EldoradoAuth
from eldorado_api import EldoradoClient
from models import ProductRow
from product_pipeline import RowConfig, process_product
from sheets_client import SheetsClient, extract_spreadsheet_id

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


# (resolved_key ghi tạm vào raw row) <- (tên cột Cell Min/Cell Max tương ứng)
# — dùng chung Link Sheet/Name Sheet, chỉ khác đúng 1 cột ô cho mỗi loại giá.
_EXTERNAL_CELL_FIELDS = [
    ("_EXTERNAL_PRICE_MIN_RESOLVED", "EXTERNAL_SHEET_CELL"),
    ("_EXTERNAL_PRICE_MAX_RESOLVED", "EXTERNAL_SHEET_CELL_MAX"),
]


def _resolve_external_cells(sheets: SheetsClient, raw_rows: list[dict[str, str]]) -> None:
    """Gom mọi dòng đã điền Link Sheet/Name Sheet + Cell Min và/hoặc Cell
    Max, đọc Price Min/Price Max trực tiếp qua Sheets API ĐÚNG 1 LẦN cho cả
    chu kỳ (không đọc riêng từng dòng) — tránh lag của công thức IMPORTRANGE
    mà vẫn không tốn thêm nhiều lượt gọi API. Gắn kết quả tạm vào raw_rows
    (khoá "_EXTERNAL_PRICE_MIN_RESOLVED"/"_EXTERNAL_PRICE_MAX_RESOLVED") —
    RowConfig.price_min/price_max ở product_pipeline.py đọc lại các khoá
    này. Chạy đồng bộ, gọi qua asyncio.to_thread từ run_one_cycle bên dưới."""
    refs_by_row_field: dict[tuple[int, str], tuple[str, str, str]] = {}
    unique_refs: set[tuple[str, str, str]] = set()
    for i, raw in enumerate(raw_rows):
        link = (raw.get("EXTERNAL_SHEET_LINK") or "").strip()
        sheet_name = (raw.get("EXTERNAL_SHEET_NAME") or "").strip()
        if not (link and sheet_name):
            continue
        spreadsheet_id = extract_spreadsheet_id(link)
        if not spreadsheet_id:
            logger.warning("[external-sheet] Dòng %d: Link Sheet không hợp lệ, không lấy được spreadsheet ID: %s", i, link)
            continue
        for resolved_key, cell_field in _EXTERNAL_CELL_FIELDS:
            cell = (raw.get(cell_field) or "").strip()
            if not cell:
                continue
            ref = (spreadsheet_id, sheet_name, cell)
            refs_by_row_field[(i, resolved_key)] = ref
            unique_refs.add(ref)

    if not unique_refs:
        return

    results = sheets.read_external_cells(list(unique_refs))
    for (i, resolved_key), ref in refs_by_row_field.items():
        value = results.get(ref)
        if value:
            raw_rows[i][resolved_key] = value


async def run_one_cycle(sheets: SheetsClient, client: EldoradoClient, semaphore: asyncio.Semaphore) -> float:
    raw_rows = await asyncio.to_thread(sheets.read_config_rows)
    await asyncio.to_thread(_resolve_external_cells, sheets, raw_rows)
    rows = [ProductRow(index=i, raw=raw) for i, raw in enumerate(raw_rows)]
    logger.info("Có %d sản phẩm trong sheet.", len(rows))

    async def guarded(row: ProductRow) -> None:
        async with semaphore:
            await process_product(row, sheets, client)
            if config.PRODUCT_DELAY_SECONDS > 0:
                await asyncio.sleep(config.PRODUCT_DELAY_SECONDS)

    await asyncio.gather(*(guarded(row) for row in rows))
    return _compute_loop_delay(rows)


async def _check_and_apply_update(client: EldoradoClient) -> None:
    """Tự kiểm tra + tự cập nhật code khi nhánh GitHub đang theo dõi có
    commit mới — CHỈ được gọi ở ĐIỂM AN TOÀN trong main() (ngay sau khi 1 chu
    kỳ repricing đã chạy xong hoàn toàn, không có task nào đang dở dang), vì
    đây là tool production (DRY_RUN có thể =False, đang chạy live) — không
    được restart giữa chừng lúc đang xử lý sản phẩm. Lỗi ở đây (mất mạng, git
    pull conflict...) chỉ log, không được làm crash vòng lặp chính."""
    try:
        if not await asyncio.to_thread(updater.check_for_update, config.GIT_BRANCH):
            return
        logger.info(
            "[updater] Phát hiện bản cập nhật mới trên GitHub (branch %s), đang pull...",
            config.GIT_BRANCH,
        )
        if not await asyncio.to_thread(updater.pull_update, config.GIT_BRANCH):
            return
    except Exception:
        logger.exception("[updater] Lỗi không mong đợi khi kiểm tra/áp dụng cập nhật GitHub — bỏ qua.")
        return

    # Từ đây trở đi KHÔNG được nuốt lỗi im lặng nữa: client sắp bị đóng, nếu
    # os.execv thất bại (vd sys.executable bị xoá/đổi chỗ, bị AV chặn...) mà
    # vẫn tiếp tục vòng lặp chính với client ĐÃ ĐÓNG, mọi sản phẩm sau đó sẽ
    # âm thầm lỗi hết (httpx báo "client has been closed") mà log không nói
    # rõ nguyên nhân gốc — thà crash tiến trình ngay để lộ lỗi rõ ràng, còn
    # hơn chạy ngầm hỏng không ai biết (tool production, DRY_RUN=False).
    logger.info("[updater] Đã pull xong — đang đóng kết nối và khởi động lại process để dùng code mới...")
    await client.aclose()
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        logger.critical(
            "[updater] os.execv thất bại SAU KHI đã đóng client — không thể tiếp tục xử lý sản phẩm an "
            "toàn nữa. Đang dừng hẳn tiến trình (cần khởi động lại tool thủ công) thay vì chạy ngầm hỏng.",
            exc_info=True,
        )
        raise


async def main() -> None:
    logger.info("Khởi động Eldorado Repricer — DRY_RUN=%s", config.DRY_RUN)
    sheets = SheetsClient()
    auth = EldoradoAuth()
    client = EldoradoClient(get_cookie=auth.get_cookie)
    semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)
    last_update_check = 0.0

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

            now = asyncio.get_event_loop().time()
            if now - last_update_check >= config.UPDATE_CHECK_INTERVAL_SECONDS:
                last_update_check = now
                await _check_and_apply_update(client)  # điểm an toàn: đã hết chu kỳ, chưa sleep

            await asyncio.sleep(loop_delay)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
