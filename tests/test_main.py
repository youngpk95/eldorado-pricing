"""Test cho main._compute_loop_delay — Relax là cấu hình cho CẢ VÒNG CHẠY
(không phải riêng 1 sản phẩm); khi nhiều sản phẩm đang bật có số khác nhau,
phải lấy số LỚN NHẤT (xác nhận trực tiếp với user 2026-09-13). Cũng test
main._check_and_apply_update (tự cập nhật từ GitHub) — đặc biệt việc KHÔNG
được nuốt lỗi im lặng nếu os.execv thất bại SAU KHI client đã bị đóng (bug đã
bị agent review phát hiện: trước đây except bọc luôn cả bước execv, khiến
vòng lặp chính tiếp tục chạy với 1 httpx client đã đóng nếu execv lỗi)."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import main  # noqa: E402
import updater  # noqa: E402
from main import _check_and_apply_update, _compute_loop_delay  # noqa: E402
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


class _FakeSheetsExternalCells:
    """Sheets giả cho test _resolve_external_cells — chỉ cần implement đúng
    read_external_cells, không cần Google credential/service thật."""

    def __init__(self, responses: dict[tuple[str, str, str], str]):
        self.responses = responses
        self.last_refs: list[tuple[str, str, str]] | None = None

    def read_external_cells(self, refs):
        self.last_refs = refs
        return {ref: self.responses.get(ref) for ref in refs}


def test_resolve_external_cells_reads_both_min_and_max():
    sheets = _FakeSheetsExternalCells({("sheet-a", "Tab1", "B2"): "10", ("sheet-a", "Tab1", "B3"): "20"})
    raw_rows = [{
        "EXTERNAL_SHEET_LINK": "sheet-a",
        "EXTERNAL_SHEET_NAME": "Tab1",
        "EXTERNAL_SHEET_CELL": "B2",
        "EXTERNAL_SHEET_CELL_MAX": "B3",
    }]

    main._resolve_external_cells(sheets, raw_rows)

    assert raw_rows[0]["_EXTERNAL_PRICE_MIN_RESOLVED"] == "10"
    assert raw_rows[0]["_EXTERNAL_PRICE_MAX_RESOLVED"] == "20"
    assert set(sheets.last_refs) == {("sheet-a", "Tab1", "B2"), ("sheet-a", "Tab1", "B3")}


def test_resolve_external_cells_only_min_filled_skips_max():
    """Cell Max để trống -> chỉ đọc Cell Min, không đọc/không gắn khoá Max
    (1 dòng có thể chỉ cần external cho Min hoặc chỉ cho Max, không bắt buộc
    cả hai)."""
    sheets = _FakeSheetsExternalCells({("sheet-a", "Tab1", "B2"): "10"})
    raw_rows = [{
        "EXTERNAL_SHEET_LINK": "sheet-a",
        "EXTERNAL_SHEET_NAME": "Tab1",
        "EXTERNAL_SHEET_CELL": "B2",
        "EXTERNAL_SHEET_CELL_MAX": "",
    }]

    main._resolve_external_cells(sheets, raw_rows)

    assert raw_rows[0]["_EXTERNAL_PRICE_MIN_RESOLVED"] == "10"
    assert "_EXTERNAL_PRICE_MAX_RESOLVED" not in raw_rows[0]
    assert sheets.last_refs == [("sheet-a", "Tab1", "B2")]


def test_resolve_external_cells_noop_when_no_link_filled():
    sheets = _FakeSheetsExternalCells({})
    raw_rows = [{"EXTERNAL_SHEET_LINK": "", "EXTERNAL_SHEET_NAME": "", "EXTERNAL_SHEET_CELL": "", "EXTERNAL_SHEET_CELL_MAX": ""}]

    main._resolve_external_cells(sheets, raw_rows)

    assert sheets.last_refs is None  # không gọi API nếu không có ref nào cần đọc
    assert raw_rows[0] == {"EXTERNAL_SHEET_LINK": "", "EXTERNAL_SHEET_NAME": "", "EXTERNAL_SHEET_CELL": "", "EXTERNAL_SHEET_CELL_MAX": ""}


@pytest.mark.asyncio
async def test_check_and_apply_update_noop_when_no_new_commit(monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", lambda branch: False)
    pull_called = []
    monkeypatch.setattr(updater, "pull_update", lambda branch: pull_called.append(branch) or True)
    client = AsyncMock()

    await _check_and_apply_update(client)

    assert pull_called == []
    client.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_apply_update_error_before_close_does_not_close_client(monkeypatch):
    """Lỗi ở bước kiểm tra/pull (vd mất mạng) chỉ log + bỏ qua — KHÔNG được
    đóng client, để chu kỳ sau vẫn xử lý sản phẩm bình thường."""
    def raise_error(branch):
        raise RuntimeError("mất mạng")

    monkeypatch.setattr(updater, "check_for_update", raise_error)
    client = AsyncMock()

    await _check_and_apply_update(client)  # không raise ra ngoài

    client.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_apply_update_pulls_closes_client_and_restarts(monkeypatch):
    monkeypatch.setattr(updater, "check_for_update", lambda branch: True)
    monkeypatch.setattr(updater, "pull_update", lambda branch: True)
    execv_calls = []
    monkeypatch.setattr(main.os, "execv", lambda executable, args: execv_calls.append((executable, args)))
    client = AsyncMock()

    await _check_and_apply_update(client)

    client.aclose.assert_awaited_once()
    assert len(execv_calls) == 1


@pytest.mark.asyncio
async def test_check_and_apply_update_reraises_when_execv_fails_after_client_closed(monkeypatch):
    """Bug đã bị agent review phát hiện: nếu os.execv thất bại SAU KHI client
    đã đóng, KHÔNG được nuốt lỗi rồi để vòng lặp chính tiếp tục chạy với
    client hỏng — phải để lỗi lan ra ngoài (crash rõ ràng thay vì âm thầm hỏng)."""
    monkeypatch.setattr(updater, "check_for_update", lambda branch: True)
    monkeypatch.setattr(updater, "pull_update", lambda branch: True)

    def broken_execv(executable, args):
        raise OSError("sys.executable không còn tồn tại")

    monkeypatch.setattr(main.os, "execv", broken_execv)
    client = AsyncMock()

    with pytest.raises(OSError):
        await _check_and_apply_update(client)

    client.aclose.assert_awaited_once()  # đã đóng trước khi execv thất bại
