"""Fixture dùng chung cho mọi test."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import offer_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_offer_cache(monkeypatch, tmp_path):
    """Mọi test (kể cả test KHÔNG liên quan tới offer_cache) có thể vô tình
    kích hoạt process_product.save_snapshot() ở nhánh fetch-offer-thành-công
    — tự động trỏ CACHE_DIR sang thư mục tmp cho MỌI test, tránh ghi file
    thật vào cache/ của repo khi chạy test suite."""
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
