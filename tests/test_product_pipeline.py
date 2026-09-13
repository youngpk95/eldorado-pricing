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
from product_pipeline import process_product  # noqa: E402


class FakeSheets:
    def __init__(self):
        self.writes: list[tuple[int, str, str | None]] = []

    def write_result(self, index: int, note: str, link: str | None) -> None:
        self.writes.append((index, note, link))


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
    index, note, link = sheets.writes[0]
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

    _, note, _ = sheets.writes[0]
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
    _, note, link = sheets.writes[0]
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
    _, note, link = sheets.writes[0]
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

    _, note, _ = sheets.writes[0]
    assert "Stock: 100 -> 5" in note


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
    index, note, link = sheets.writes[0]
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

    _, _, link = sheets.writes[0]
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
    _, note, link = sheets.writes[0]
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
    _, note, link = sheets.writes[0]
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
    _, note, link = sheets.writes[0]
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
    _, note, link = sheets.writes[0]
    assert "[DRY RUN]" in note
    assert link is None
