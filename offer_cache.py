"""Lưu snapshot offer đã fetch THÀNH CÔNG gần nhất cho mỗi dòng sheet — dùng
để tự tạo lại offer khi gặp 404 (offer đã bị xoá) mà không còn dữ liệu tươi
trong bộ nhớ (404 phát hiện ở 1 CHU KỲ SAU, không phải ngay lúc offer mất).

Bug thật đã gặp (2026-09-13, dòng "Mirror COTA"): 429 -> xoá + tạo lại offer
mới thành công, nhưng việc ghi link mới về sheet thất bại -> chu kỳ sau vẫn
đọc link CŨ đã xoá -> 404 mãi mãi, phải sửa tay. Snapshot này cho phép tool
tự phục hồi thay vì kẹt vĩnh viễn — xem product_pipeline.py._recover_missing_offer.

Mỗi dòng 1 file riêng (KHÔNG gộp 1 file JSON chung) — tránh race condition
khi nhiều dòng được xử lý song song (asyncio.gather, xem product_pipeline.py)
cùng ghi cache 1 lúc."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from models import OwnOffer

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / "cache"


def _cache_path(row_index: int) -> Path:
    return CACHE_DIR / f"row_{row_index}.json"


def save_snapshot(row_index: int, own_listing_url: str, offer: OwnOffer) -> None:
    """Ghi đè snapshot của dòng này — gọi mỗi khi fetch offer THÀNH CÔNG,
    kèm chính URL đang dùng để đối chiếu sau này (xem load_snapshot). Luôn
    xoá `pending_recreated_link` cũ (nếu có) — fetch thành công nghĩa là
    sheet đã trỏ đúng offer sống, không còn gì "đang chờ phục hồi" nữa."""
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        payload: dict[str, Any] = asdict(offer)
        payload["price"] = str(offer.price)  # Decimal không tự serialize JSON được
        data = {"own_listing_url": own_listing_url, "offer": payload, "pending_recreated_link": None}
        _cache_path(row_index).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError) as e:
        # TypeError: raw_offer lưu nguyên từ response Eldorado (product_pipeline.py)
        # — nếu lỡ chứa giá trị json.dumps không serialize được, đây CHỈ là
        # cache best-effort, không được để lỗi này làm rớt cả lượt xử lý dòng
        # (nếu không bắt, nó sẽ văng lên tới except Exception chung của
        # process_product, biến 1 lỗi cache vô hại thành "Lỗi không xác định").
        logger.warning("[cache] Không ghi được snapshot dòng %s: %s", row_index, e)


def load_snapshot(row_index: int, expected_own_listing_url: str) -> tuple[OwnOffer, str | None] | None:
    """Trả về (offer đã lưu, link đã tự tạo lại nhưng CHƯA xác nhận ghi được
    vào sheet — None nếu chưa có lần thử nào) — CHỈ khi `expected_own_listing_url`
    khớp ĐÚNG với URL lúc snapshot được lưu (tránh dùng nhầm snapshot của dòng
    khác, hoặc của 1 URL vừa bị sửa tay sang offer khác hẳn). None nếu không
    có snapshot hợp lệ."""
    path = _cache_path(row_index)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[cache] Không đọc được snapshot dòng %s: %s", row_index, e)
        return None

    if data.get("own_listing_url") != expected_own_listing_url:
        return None

    offer_data = dict(data.get("offer") or {})
    try:
        offer_data["price"] = Decimal(offer_data["price"])
        offer = OwnOffer(**offer_data)
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("[cache] Snapshot dòng %s hỏng cấu trúc: %s", row_index, e)
        return None

    return offer, data.get("pending_recreated_link")


def mark_pending_link(row_index: int, link: str) -> None:
    """Ghi lại link vừa tự tạo lại nhưng CHƯA chắc đã lưu được vào sheet —
    'chốt an toàn' chống tạo trùng: lần sau vẫn gặp 404, nếu thấy đã có
    pending link thì KHÔNG tạo thêm offer mới nữa, chỉ thử ghi lại link cũ
    vào sheet (xem product_pipeline.py._recover_missing_offer)."""
    path = _cache_path(row_index)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["pending_recreated_link"] = link
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[cache] Không đánh dấu pending link dòng %s: %s", row_index, e)
