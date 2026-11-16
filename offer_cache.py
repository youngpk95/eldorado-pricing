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


# ---------------------------------------------------------------------------
# Tạm dừng TẠO offer mới khi Eldorado báo hết quota (dùng chung mọi dòng)
# ---------------------------------------------------------------------------
# Bug thật đã gặp (2026-11-14, sheet "Eldo D4 V3", dòng "Mirror COU"): POST tạo
# offer trả 429 "Whoa! Calm down, cowboy! Quota exceeded" — quota tạo offer tính
# cho CẢ tài khoản/client của tool, không theo từng dòng. Tool cứ mỗi chu kỳ
# (~45s) lại POST tạo lại cho 2 dòng bị 404 -> quota không bao giờ hồi. Lưu mốc
# "chặn tới lúc nào" ra FILE (không chỉ trong RAM) để tool khởi động lại /
# tự cập nhật cũng không POST lại ngay.

def _cooldown_path() -> Path:
    return CACHE_DIR / "create_cooldown.json"


def _read_create_state() -> dict[str, Any]:
    path = _cooldown_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[cache] Không đọc được create_cooldown.json: %s", e)
        return {}


def _write_create_state(data: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _cooldown_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("[cache] Không ghi được create_cooldown.json: %s", e)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def get_create_cooldown_until(now: float) -> float | None:
    """Trả về mốc epoch (giây) đang chặn tạo offer, None nếu không bị chặn."""
    until = _as_float(_read_create_state().get("until"))
    return until if until > now else None


def record_create_429(now: float, base: float, cap: float, retry_after: float | None, reason: str) -> float:
    """Ghi nhận 1 lần TẠO offer bị 429 liên tiếp -> trả về mốc được thử lại.
    Chờ tăng dần base * 2^(số lần 429 liên tiếp - 1), tối đa `cap`; nếu
    Eldorado gửi Retry-After thì dùng đúng giá trị đó. `reason` = body 429,
    chỉ để mở file ra xem tay khi cần chẩn đoán."""
    data = _read_create_state()
    fails = int(_as_float(data.get("fail_count"))) + 1
    wait = retry_after if retry_after is not None else min(cap, base * 2 ** (fails - 1))
    until = now + wait
    data.update({"until": until, "fail_count": fails, "reason": reason})
    _write_create_state(data)
    return until


def record_create_success() -> None:
    """Tạo offer thành công -> Eldorado đã cho tạo lại, xoá ngay mọi chờ."""
    data = _read_create_state()
    if data.get("until") or data.get("fail_count"):
        data.update({"until": 0, "fail_count": 0, "reason": None})
        _write_create_state(data)


def get_last_create_at() -> float:
    """Mốc lần GỬI POST tạo offer gần nhất — lưu file để khởi động lại / tự
    cập nhật cũng không tạo dồn ngay (xem writer._reserve_create_slot)."""
    return _as_float(_read_create_state().get("last_create_at"))


def set_last_create_at(ts: float) -> None:
    data = _read_create_state()
    data["last_create_at"] = ts
    _write_create_state(data)


# ---------------------------------------------------------------------------
# Tạm dừng MỌI lệnh GHI (đổi giá/sửa/tạo) khi Eldorado báo 429 (dùng chung mọi dòng)
# ---------------------------------------------------------------------------
# 2026-11-16: khi "Quota exceeded", trước đây dòng nào, chu kỳ nào cũng vẫn gửi
# 1-2 lệnh ghi rồi nhận 429 -> phí lệnh, có thể còn kéo dài thời gian bị chặn
# (14/11 bị chặn > 1 tiếng). Giờ: 1 lệnh ghi bị 429 -> mọi dòng ngừng ghi
# tới mốc `until` (chờ tăng dần như khi tạo bị 429); hết hạn thì dòng đầu tiên
# ghi lại chính là lần "thử"; ghi thành công là xoá chờ ngay. File riêng, không
# đụng create_cooldown.json.

def _write_pause_path() -> Path:
    return CACHE_DIR / "write_cooldown.json"


def _read_write_pause() -> dict[str, Any]:
    path = _write_pause_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[cache] Không đọc được write_cooldown.json: %s", e)
        return {}


def _save_write_pause(data: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _write_pause_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("[cache] Không ghi được write_cooldown.json: %s", e)


def get_write_pause_until(now: float) -> float | None:
    """Mốc epoch (giây) đang tạm dừng ghi, None nếu được ghi bình thường."""
    until = _as_float(_read_write_pause().get("until"))
    return until if until > now else None


def record_write_429(now: float, base: float, cap: float, retry_after: float | None, reason: str) -> float:
    """1 lệnh ghi bị 429 -> tạm dừng ghi. Chờ base * 2^(số lần liên tiếp - 1),
    tối đa `cap`, hoặc đúng Retry-After nếu Eldorado gửi. Nhiều dòng cùng bị
    429 trong lúc đang dừng thì KHÔNG cộng dồn (chỉ tăng khi lần thử sau khi
    hết dừng vẫn 429)."""
    data = _read_write_pause()
    current_until = _as_float(data.get("until"))
    if current_until > now:
        return current_until
    fails = int(_as_float(data.get("fail_count"))) + 1
    wait = retry_after if retry_after is not None else min(cap, base * 2 ** (fails - 1))
    until = now + wait
    data.update({"until": until, "fail_count": fails, "reason": reason})
    _save_write_pause(data)
    return until


def record_write_success() -> None:
    """Ghi thành công -> Eldorado đã cho ghi lại, xoá ngay mọi chờ."""
    data = _read_write_pause()
    if data.get("until") or data.get("fail_count"):
        data.update({"until": 0, "fail_count": 0, "reason": None})
        _save_write_pause(data)
