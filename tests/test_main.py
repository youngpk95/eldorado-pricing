"""Test cho main._compute_loop_delay — Relax là cấu hình cho CẢ VÒNG CHẠY
(không phải riêng 1 sản phẩm); khi nhiều sản phẩm đang bật có số khác nhau,
phải lấy số LỚN NHẤT (xác nhận trực tiếp với user 2026-09-13)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from main import _compute_loop_delay  # noqa: E402
from models import ProductRow  # noqa: E402


def _row(index: int, enabled: str, relax: str) -> ProductRow:
    raw = {
        "ENABLED": enabled,
        "OWN_LISTING_URL": "https://www.eldorado.gg/dashboard/offers/Currency/edit/abc",
        "COMPARE_URL": "https://www.eldorado.gg/some-game/i/1",
        "RELAX_SECONDS": relax,
    }
    return ProductRow(index=index, raw=raw)


def test_uses_default_when_no_row_has_relax_set(monkeypatch):
    monkeypatch.setattr(config, "LOOP_DELAY_SECONDS", 30)
    rows = [_row(0, "TRUE", "")]
    assert _compute_loop_delay(rows) == 30


def test_uses_max_relax_among_enabled_rows():
    rows = [_row(0, "TRUE", "60"), _row(1, "TRUE", "120"), _row(2, "TRUE", "10")]
    assert _compute_loop_delay(rows) == 120


def test_ignores_relax_from_disabled_rows():
    rows = [_row(0, "TRUE", "60"), _row(1, "FALSE", "9999")]
    assert _compute_loop_delay(rows) == 60


def test_ignores_relax_from_rows_missing_required_links(monkeypatch):
    monkeypatch.setattr(config, "LOOP_DELAY_SECONDS", 30)
    # ENABLED tích nhưng thiếu link -> cfg.enabled=False (xem RowConfig.enabled)
    broken = ProductRow(index=0, raw={"ENABLED": "TRUE", "OWN_LISTING_URL": "", "COMPARE_URL": "", "RELAX_SECONDS": "999"})
    assert _compute_loop_delay([broken]) == 30
