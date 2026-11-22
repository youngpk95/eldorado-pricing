"""Fixture dùng chung cho mọi test."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import offer_cache  # noqa: E402
import writer  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_offer_cache(monkeypatch, tmp_path):
    """Mọi test (kể cả test KHÔNG liên quan tới offer_cache) có thể vô tình
    kích hoạt process_product.save_snapshot() ở nhánh fetch-offer-thành-công
    — tự động trỏ CACHE_DIR sang thư mục tmp cho MỌI test, tránh ghi file
    thật vào cache/ của repo khi chạy test suite."""
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _reset_create_spacing(monkeypatch):
    """Mốc 'lần tạo offer gần nhất' của writer là trạng thái toàn tiến trình —
    lưu trong cache/ (đã trỏ sang tmp ở fixture trên) — chỉ cần reset lock."""
    monkeypatch.setattr(writer, "_create_lock", None)
    writer._offer_block_until.clear()
    # Không để test thật sự ngủ 5s giữa xoá và tạo offer.
    monkeypatch.setattr(writer.config, "RECREATE_STEP_DELAY_SECONDS", 0)
