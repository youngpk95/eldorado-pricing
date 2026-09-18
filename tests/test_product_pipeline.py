"""Test tích hợp cho product_pipeline.process_product — mock EldoradoClient
(không gọi mạng thật) + SheetsClient giả (ghi vào list trong RAM), kiểm tra
toàn bộ luồng nối các module lại với nhau chạy đúng, không chỉ riêng từng hàm
thuần tuý trong pricing.py."""
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (đảm bảo DRY_RUN mặc định True cho test)
import offer_cache  # noqa: E402
from eldorado_api import OfferNotFoundError  # noqa: E402
from models import OfferUrls, OwnOffer, ProductRow  # noqa: E402
from product_pipeline import RowConfig, process_product  # noqa: E402


class FakeSheets:
    def __init__(self):
        self.writes: list[tuple[int, str, str | None, str]] = []

    def write_result(self, index: int, note: str, link: str | None, expected_own_url: str) -> None:
        self.writes.append((index, note, link, expected_own_url))


def make_own_offer(price="120", quantity=200, min_quantity=1) -> OwnOffer:
    return OwnOffer(
        offer_id="abc123",
        offer_type="Currency",
        category="Currency",
        game_id="201",
        price=Decimal(price),
        quantity=quantity,
        min_quantity=min_quantity,
        description="desc",
        offer_title="My Offer",
        guaranteed_delivery_time="instant",
        delivery_method="",
        trade_environment_values=[],
        attributes_raw=[],
        raw_offer={"gameId": "201", "category": "Currency", "attributes": [], "volumeDiscounts": []},
    )


def make_row(**overrides) -> ProductRow:
    raw = {
        "ENABLED": "TRUE",  # giá trị thật của checkbox (Data Validation BOOLEAN), không phải "1"
        "NAME": "Test Product",
        "OWN_LISTING_URL": "https://www.eldorado.gg/dashboard/offers/Currency/edit/abc123",
        "COMPARE_URL": "https://www.eldorado.gg/some-game/i/201-2-0",
        "STOCK": "100",
        "PRICE_MIN": "100", "PRICE_MAX": "", "DISCOUNT_AMOUNT": "0.01",
        "ROUND_DECIMALS": "2", "ALWAYS_UNDERCUT": "1",
        "MIN_PURCHASE_BASE": "", "MIN_PURCHASE_COEF": "1",
        "COMPETITOR_STOCK_MIN": "", "COMPETITOR_MIN_RATING_COUNT": "",
        "COMPETITOR_MIN_FEEDBACK_PERCENT": "", "SELLER_BLACKLIST": "",
        "ALLOW_RECREATE_ON_RATE_LIMIT": "0",
    }
    raw.update(overrides)
    return ProductRow(index=0, raw=raw)


@pytest.mark.asyncio
async def test_process_product_undercuts_cheapest_qualifying_competitor(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row()  # STOCK=100 > STOCK_LIMIT=10 -> has_stock; PRICE_MIN=100 loại "TooCheap"

    own_offer = make_own_offer(price="120")
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
        {
            "user": {"username": "TooCheap"},
            "offer": {"pricePerUnit": {"amount": 10}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    assert len(sheets.writes) == 1
    index, note, link, _ = sheets.writes[0]
    assert index == 0
    assert link is None  # DRY_RUN=True -> không tạo offer mới
    assert "[DRY RUN]" in note
    # PRICE_MIN=100 loại "TooCheap"=10 khỏi danh sách hợp lệ (chỉ còn cảnh báo)
    # -> đối thủ hợp lệ rẻ nhất là Rival=113.98, trừ discount 0.01, làm tròn xuống 2 số.
    assert "113.97" in note
    assert "Đối thủ rẻ nhất hợp lệ: Rival" in note
    assert "TooCheap" in note  # vẫn liệt kê ở mục cảnh báo "giá thấp hơn sàn"


@pytest.mark.asyncio
async def test_process_product_floor_none_disables_floor_filtering_entirely(monkeypatch):
    """Xác nhận rủi ro mà agent review đã cảnh báo: khi floor (PRICE_MIN) để
    trống, MỌI đối thủ đều được coi là 'hợp lệ' — kể cả giá cực thấp bất
    thường — vì không có gì để lọc theo. Đây là hành vi port ĐÚNG từ bản gốc
    (không phải bug mới), nhưng cần 1 test rõ ràng để không ai vô tình sửa
    sai hướng sau này mà không nhận ra."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(PRICE_MIN="")  # không có giá sàn -> floor=None

    own_offer = make_own_offer(price="120")
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")
    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
        {
            "user": {"username": "TooCheap"},
            "offer": {"pricePerUnit": {"amount": 10}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    _, note, _, _ = sheets.writes[0]
    assert "Đối thủ rẻ nhất hợp lệ: TooCheap" in note


@pytest.mark.asyncio
async def test_process_product_skips_when_enabled_flag_off():
    row = make_row(ENABLED="FALSE")  # checkbox chưa tích
    sheets = FakeSheets()
    client = AsyncMock()

    await process_product(row, sheets, client)

    assert sheets.writes == []
    client.get_own_offer.assert_not_called()


@pytest.mark.asyncio
async def test_process_product_reports_error_when_enabled_but_missing_url(monkeypatch):
    """Bug thật phát hiện qua agent review (2026-09-13): trước đây tích
    Enabled nhưng thiếu link bị bỏ qua HOÀN TOÀN im lặng, không ghi gì vào
    Status — staff không biết vì sao dòng không chạy. Giờ phải báo lỗi rõ."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(ENABLED="TRUE", OWN_LISTING_URL="", COMPARE_URL="")
    sheets = FakeSheets()
    client = AsyncMock()

    await process_product(row, sheets, client)

    assert len(sheets.writes) == 1
    _, note, link, _ = sheets.writes[0]
    assert "thiếu" in note.lower()
    assert link is None
    client.get_own_offer.assert_not_called()


@pytest.mark.asyncio
async def test_process_product_accepts_legacy_1_for_enabled(monkeypatch):
    """Chấp nhận cả '1' (kiểu nhập tay cũ) lẫn 'TRUE' (checkbox thật) —
    không phụ thuộc duy nhất 1 định dạng."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(ENABLED="1")
    own_offer = make_own_offer(price="113.97", quantity=100)
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")
    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = []

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    assert len(sheets.writes) == 1  # đã xử lý, không bị coi là tắt


@pytest.mark.asyncio
async def test_process_product_no_change_when_price_already_optimal(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row()

    # Giá hiện tại ĐÃ bằng đúng giá sẽ tính ra (đối thủ rẻ nhất - discount, làm tròn xuống 2 số).
    own_offer = make_own_offer(price="113.97", quantity=100)
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    assert len(sheets.writes) == 1
    _, note, link, _ = sheets.writes[0]
    assert "Không có thay đổi" in note
    assert link is None


@pytest.mark.asyncio
async def test_process_product_syncs_stock_quantity(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(STOCK="5")  # khác quantity hiện tại của offer (100) -> phải đồng bộ

    own_offer = make_own_offer(price="113.97", quantity=100)
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    _, note, _, _ = sheets.writes[0]
    assert "Stock: 100 -> 5" in note


@pytest.mark.asyncio
async def test_process_product_holds_stock_below_min_stock_update_threshold(monkeypatch):
    """offer.quantity=100 vẫn >= MIN_STOCK_UPDATE=50 -> chưa xuống ngưỡng,
    bỏ qua update stock dù Stock sheet=5 khác hẳn quantity hiện tại. Giá đã
    tối ưu sẵn (113.97) + không có Min Purchase Base -> không còn thay đổi
    nào khác -> dòng báo 'Không có thay đổi'."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(STOCK="5", MIN_STOCK_UPDATE="50")

    own_offer = make_own_offer(price="113.97", quantity=100)
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    _, note, link, _ = sheets.writes[0]
    assert "Stock: 100 -> 100" in note
    assert "⏸️" in note and "Min Stock Update=50" in note
    assert "Không có thay đổi" in note
    assert link is None


@pytest.mark.asyncio
async def test_process_product_updates_stock_when_below_min_stock_update_threshold(monkeypatch):
    """offer.quantity=100 < MIN_STOCK_UPDATE=150 -> đã xuống dưới ngưỡng,
    đồng bộ stock bình thường như khi không có ngưỡng."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(STOCK="5", MIN_STOCK_UPDATE="150")

    own_offer = make_own_offer(price="113.97", quantity=100)
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    _, note, _, _ = sheets.writes[0]
    assert "Stock: 100 -> 5" in note
    assert "⏸️" not in note


@pytest.mark.asyncio
async def test_process_product_still_updates_price_while_stock_held(monkeypatch):
    """Ngưỡng Min Stock Update chỉ chặn riêng phần stock — Price vẫn phải
    tính/update độc lập, không bị bỏ qua theo (yêu cầu đã xác nhận với
    user: chỉ giữ stock, không giữ cả dòng)."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    row = make_row(STOCK="5", MIN_STOCK_UPDATE="50")

    own_offer = make_own_offer(price="120", quantity=100)  # giá cần đổi
    urls = OfferUrls(detail="d", compare="c", update="u", change="ch")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    _, note, _, _ = sheets.writes[0]
    assert "[DRY RUN]" in note
    assert "113.97" in note  # giá mới vẫn được tính ra bình thường
    assert "Stock: 100 -> 100" in note  # stock bị giữ, không theo Stock sheet=5
    assert "⏸️" in note


class FakeCreateResponse:
    """Giả lập httpx.Response cho request POST tạo offer mới (không cần đủ
    thuộc tính như FakeResponse của test_writer.py, chỉ dùng ở đây)."""

    def __init__(self, status_code: int, offer_id: str | None = None):
        self.status_code = status_code
        self.text = ""
        self._offer_id = offer_id

    def json(self):
        return {"offer": {"id": self._offer_id}} if self._offer_id else {}


@pytest.mark.asyncio
async def test_process_product_recovers_from_snapshot_when_offer_not_found(monkeypatch, tmp_path):
    """Bug thật đã gặp (2026-09-13, dòng "Mirror COTA"): offer bị xoá (404)
    mà sheet vẫn trỏ link cũ -> trước đây kẹt vĩnh viễn. Giờ phải tự tạo lại
    từ snapshot + dán link mới, KHÔNG được gọi delete (offer gốc đã mất từ
    trước, không phải do lần chạy này xoá)."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)

    row = make_row(ALLOW_RECREATE_ON_RATE_LIMIT="1")
    offer_cache.save_snapshot(row.index, row.get("OWN_LISTING_URL"), make_own_offer(price="120", quantity=100))

    client = AsyncMock()
    client.get_own_offer.side_effect = OfferNotFoundError("Không tìm thấy ID sản phẩm")
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]
    client.post.return_value = FakeCreateResponse(201, offer_id="new-offer-id")

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    assert len(sheets.writes) == 1
    index, note, link, _ = sheets.writes[0]
    assert index == 0
    assert link == "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-offer-id"
    assert "TỰ TẠO LẠI" in note
    client.delete.assert_not_called()

    # Đã lưu pending link vào cache -> lần 404 kế tiếp không được tạo trùng nữa.
    _, pending_link = offer_cache.load_snapshot(row.index, row.get("OWN_LISTING_URL"))
    assert pending_link == link


@pytest.mark.asyncio
async def test_process_product_marks_pending_link_after_429_recreate(monkeypatch):
    """Bug thật đã gặp (2026-09-13, dòng "Mirror COTA") — và phát hiện lại
    qua code review sau lần sửa đầu tiên: 429 -> xoá + tạo lại offer mới
    thành công phải đánh dấu pending link NGAY tại đây (không chỉ ở luồng
    phục hồi 404 _recover_missing_offer) — nếu không, khi ghi sheet thất bại
    và chu kỳ sau gặp 404, tool sẽ tạo THÊM 1 offer trùng nữa thay vì phát
    hiện ra đã có 1 cái đang chờ."""
    monkeypatch.setattr(config, "DRY_RUN", False)

    row = make_row(STOCK="5", ALLOW_RECREATE_ON_RATE_LIMIT="1")  # STOCK khác offer -> ép dùng full update, không phải price-only
    own_offer = make_own_offer(price="120", quantity=100)
    urls = OfferUrls(detail="d", compare="c", update="https://api/update", change="https://api/change")

    client = AsyncMock()
    client.get_own_offer.return_value = (own_offer, urls)
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = [
        {
            "user": {"username": "Rival"},
            "offer": {"pricePerUnit": {"amount": 113.98}, "quantity": 50, "guaranteedDeliveryTime": "instant", "offerTitle": ""},
            "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
        },
    ]
    client.put.return_value = FakeCreateResponse(429)
    client.delete.return_value = FakeCreateResponse(200)
    client.post.return_value = FakeCreateResponse(201, offer_id="recreated-id")

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    _, _, link, _ = sheets.writes[0]
    assert link == "https://www.eldorado.gg/dashboard/offers/Currency/edit/recreated-id"

    _, pending_link = offer_cache.load_snapshot(row.index, row.get("OWN_LISTING_URL"))
    assert pending_link == link


@pytest.mark.asyncio
async def test_process_product_does_not_recreate_twice_when_pending_link_exists(monkeypatch, tmp_path):
    """Chốt an toàn chống spam: đã tự tạo lại 1 lần (pending link có sẵn) —
    lần 404 SAU không được gọi POST tạo thêm offer nữa, chỉ thử ghi lại
    đúng link cũ vào sheet."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)

    row = make_row(ALLOW_RECREATE_ON_RATE_LIMIT="1")
    offer_cache.save_snapshot(row.index, row.get("OWN_LISTING_URL"), make_own_offer())
    pending = "https://www.eldorado.gg/dashboard/offers/Currency/edit/already-created"
    offer_cache.mark_pending_link(row.index, pending)

    client = AsyncMock()
    client.get_own_offer.side_effect = OfferNotFoundError("Không tìm thấy ID sản phẩm")

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    client.post.assert_not_called()
    client.delete.assert_not_called()
    _, note, link, _ = sheets.writes[0]
    assert link == pending


@pytest.mark.asyncio
async def test_process_product_reports_manual_fix_when_no_snapshot_available(monkeypatch, tmp_path):
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    row = make_row(ALLOW_RECREATE_ON_RATE_LIMIT="1")
    client = AsyncMock()
    client.get_own_offer.side_effect = OfferNotFoundError("Không tìm thấy ID sản phẩm")

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    client.post.assert_not_called()
    _, note, link, _ = sheets.writes[0]
    assert link is None
    assert "tự tạo lại tay" in note.lower()


@pytest.mark.asyncio
async def test_process_product_reports_error_when_recreate_disabled(monkeypatch, tmp_path):
    """ALLOW_RECREATE_ON_RATE_LIMIT chưa bật -> KHÔNG được tự ý tạo offer
    mới dù có sẵn snapshot, chỉ báo lỗi."""
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    row = make_row(ALLOW_RECREATE_ON_RATE_LIMIT="0")
    offer_cache.save_snapshot(row.index, row.get("OWN_LISTING_URL"), make_own_offer())

    client = AsyncMock()
    client.get_own_offer.side_effect = OfferNotFoundError("Không tìm thấy ID sản phẩm")

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    client.post.assert_not_called()
    _, note, link, _ = sheets.writes[0]
    assert link is None
    assert "Allow Recreate" in note


@pytest.mark.asyncio
async def test_process_product_dry_run_recovery_skips_real_create(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    row = make_row(ALLOW_RECREATE_ON_RATE_LIMIT="1")
    offer_cache.save_snapshot(row.index, row.get("OWN_LISTING_URL"), make_own_offer())

    client = AsyncMock()
    client.get_own_offer.side_effect = OfferNotFoundError("Không tìm thấy ID sản phẩm")
    client.build_compare_url = MagicMock(return_value="https://api/compare")
    client.get_competitors.return_value = []

    sheets = FakeSheets()
    await process_product(row, sheets, client)

    client.post.assert_not_called()
    _, note, link, _ = sheets.writes[0]
    assert "[DRY RUN]" in note
    assert link is None


def test_row_config_price_min_uses_local_value_when_no_external_columns():
    """Không điền Link Sheet/Name Sheet/Cell Min -> hành vi y hệt trước đây,
    đọc thẳng cột Price Min tại chỗ."""
    row = make_row(PRICE_MIN="99.5")
    cfg = RowConfig(row)

    assert cfg.has_external_price_min is False
    assert cfg.price_min == Decimal("99.5")
    assert cfg.external_price_min_warning is None


def test_row_config_price_min_uses_external_resolved_value_when_present():
    """Đã điền đủ 3 cột external VÀ main.py đã đọc thành công (gắn vào
    _EXTERNAL_PRICE_MIN_RESOLVED) -> ưu tiên giá trị đó, không dùng
    Price Min tại chỗ dù có điền."""
    row = make_row(
        PRICE_MIN="10",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL="B2",
        _EXTERNAL_PRICE_MIN_RESOLVED="42.5",
    )
    cfg = RowConfig(row)

    assert cfg.has_external_price_min is True
    assert cfg.price_min == Decimal("42.5")
    assert cfg.external_price_min_warning is None


def test_row_config_price_min_falls_back_to_local_when_external_unresolved():
    """Đọc external thất bại (chưa share quyền, sai tab, sai ô...) -> fallback
    dùng Price Min tại chỗ, KÈM cảnh báo rõ ràng để staff biết mà sửa."""
    row = make_row(
        PRICE_MIN="10",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL="B2",
    )
    cfg = RowConfig(row)

    assert cfg.price_min == Decimal("10")
    assert cfg.external_price_min_warning is not None
    assert "chưa share" in cfg.external_price_min_warning.lower() or "10" in cfg.external_price_min_warning


def test_row_config_external_price_min_warning_present_when_resolved_value_not_numeric():
    """Bug phát hiện qua code review: ô external đọc được (không rỗng) nhưng
    KHÔNG phải số (vd '#REF!', 'N/A', gõ nhầm ô chứa chữ) trước đây bị coi là
    "đã resolved" nên KHÔNG có cảnh báo dù thực chất đang âm thầm fallback về
    Price Min tại chỗ — giờ phải luôn có cảnh báo trong trường hợp này."""
    row = make_row(
        PRICE_MIN="10",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL="B2",
        _EXTERNAL_PRICE_MIN_RESOLVED="#REF!",
    )
    cfg = RowConfig(row)

    assert cfg.price_min == Decimal("10")  # vẫn fallback đúng
    assert cfg.external_price_min_warning is not None  # nhưng PHẢI có cảnh báo


def test_row_config_price_max_uses_external_resolved_value_when_present():
    """Price Max dùng chung Link Sheet/Name Sheet với Price Min, nhưng đọc
    qua cột Cell Max riêng — độc lập với việc Price Min có bật external hay
    không (1 dòng có thể chỉ bật external cho 1 trong 2)."""
    row = make_row(
        PRICE_MAX="200",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL_MAX="B3",
        _EXTERNAL_PRICE_MAX_RESOLVED="150",
    )
    cfg = RowConfig(row)

    assert cfg.has_external_price_max is True
    assert cfg.has_external_price_min is False  # không điền Cell Min -> Price Min vẫn đọc tại chỗ
    assert cfg.price_max == Decimal("150")
    assert cfg.external_price_max_warning is None


def test_row_config_price_max_falls_back_to_local_when_external_unresolved():
    row = make_row(
        PRICE_MAX="200",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL_MAX="B3",
    )
    cfg = RowConfig(row)

    assert cfg.price_max == Decimal("200")
    assert cfg.external_price_max_warning is not None


def test_row_config_min_and_max_external_resolve_independently():
    """Cả 2 cùng bật, 1 cái đọc thành công 1 cái lỗi — mỗi cái phải fallback
    ĐỘC LẬP, không được để 1 cái lỗi kéo cái kia theo."""
    row = make_row(
        PRICE_MIN="10", PRICE_MAX="200",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL="B2", EXTERNAL_SHEET_CELL_MAX="B3",
        _EXTERNAL_PRICE_MIN_RESOLVED="42.5",
        # Price Max KHÔNG resolve -> phải fallback về PRICE_MAX tại chỗ
    )
    cfg = RowConfig(row)

    assert cfg.price_min == Decimal("42.5")
    assert cfg.external_price_min_warning is None
    assert cfg.price_max == Decimal("200")
    assert cfg.external_price_max_warning is not None


def test_row_config_price_min_none_and_warns_correctly_when_local_fallback_not_numeric():
    """Bug phát hiện qua code review: nếu external lỗi VÀ cột Price Min tại
    chỗ cũng không phải số (vd gõ nhầm chữ), price_min phải là None (KHÔNG
    có giá sàn) — cảnh báo KHÔNG được tuyên bố sai là "đang dùng tạm" giá trị
    đó, vì giá trị đó không parse được thành số."""
    row = make_row(
        PRICE_MIN="không phải số",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL="B2",
    )
    cfg = RowConfig(row)

    assert cfg.price_min is None
    assert cfg.external_price_min_warning is not None
    assert "Đang dùng tạm" not in cfg.external_price_min_warning


def test_row_config_price_min_none_when_external_unresolved_and_no_local_fallback():
    """Đọc external thất bại VÀ Price Min tại chỗ cũng trống -> None (không
    có giá sàn), nhưng vẫn phải có cảnh báo khác với trường hợp có fallback."""
    row = make_row(
        PRICE_MIN="",
        EXTERNAL_SHEET_LINK="https://docs.google.com/spreadsheets/d/abc123/edit",
        EXTERNAL_SHEET_NAME="Tab1",
        EXTERNAL_SHEET_CELL="B2",
    )
    cfg = RowConfig(row)

    assert cfg.price_min is None
    assert cfg.external_price_min_warning is not None
    assert "trống" in cfg.external_price_min_warning


def test_row_config_title_keyword_filters_parsed_lowercase_and_split_by_semicolon():
    row = make_row(TITLE_EXCLUDE_KEYWORDS=" Bot ; Farm", TITLE_REQUIRE_KEYWORDS="Gold")
    cfg = RowConfig(row)

    assert cfg.title_exclude_keywords == {"bot", "farm"}
    assert cfg.title_require_keywords == {"gold"}


def test_row_config_title_keyword_filters_empty_by_default():
    row = make_row()
    cfg = RowConfig(row)

    assert cfg.title_exclude_keywords == set()
    assert cfg.title_require_keywords == set()
