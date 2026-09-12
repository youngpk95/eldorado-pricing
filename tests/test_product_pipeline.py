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
