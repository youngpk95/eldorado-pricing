"""Test luồng xử lý 429 (quá tải) của writer.apply_update — giả lập response
HTTP, KHÔNG gọi Eldorado thật (không có cách nào an toàn/hợp lệ để cố tình
làm tài khoản thật bị 429). Theo xác nhận trực tiếp từ user (2026-09-13):
tạo offer MỚI không bao giờ bị 429 (chỉ endpoint đổi giá/update mới bị) —
nên chỉ cần đúng 1 lần tạo, không có retry loop."""
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import OfferUrls, OwnOffer  # noqa: E402
from writer import apply_update  # noqa: E402


@dataclass
class FakeResponse:
    status_code: int
    text: str = ""
    _json: dict = field(default_factory=dict)

    def json(self):
        return self._json


def make_offer() -> OwnOffer:
    return OwnOffer(
        offer_id="old-id-123",
        offer_type="Currency",
        category="Currency",
        game_id="220",
        price=Decimal("0.0016"),
        quantity=458650,
        min_quantity=1500,
        description="Fast delivery, 24/7 online",
        offer_title="Divine Orb",
        guaranteed_delivery_time="instant",
        delivery_method="",
        trade_environment_values=[{"id": "te1", "value": "Standard"}],
        attributes_raw=[],
        raw_offer={"gameId": "220", "category": "Currency", "attributes": [], "volumeDiscounts": []},
    )


def make_urls() -> OfferUrls:
    return OfferUrls(detail="d", compare="c", update="https://api/update", change="https://api/change")


@pytest.mark.asyncio
async def test_normal_price_change_succeeds_without_touching_create():
    client = AsyncMock()
    client.put.return_value = FakeResponse(200)

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is True
    assert link is None
    client.post.assert_not_called()
    client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_429_triggers_single_recreate_with_new_price_and_returns_new_link():
    client = AsyncMock()
    # change-price VÀ update đầy đủ đều bị 429 (mô phỏng đúng ảnh user gửi)
    client.put.side_effect = [FakeResponse(429, text="Whoa! Calm down, cowboy!"), FakeResponse(429)]
    client.delete.return_value = FakeResponse(200)
    # Tạo offer mới THÀNH CÔNG ngay lần đầu (đúng như user xác nhận: tạo mới
    # không bao giờ bị 429).
    client.post.return_value = FakeResponse(201, _json={"offer": {"id": "new-id-456"}})

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is True
    assert link == "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id-456"
    assert client.post.call_count == 1  # đúng 1 lần tạo, không có retry loop
    # Category Currency -> xoá TRƯỚC khi tạo (should_delete_before=True)
    assert client.delete.call_count == 1

    # Payload gửi lên phải mang GIÁ MỚI (không phải giá cũ rồi đổi sau)
    create_call = client.post.call_args
    sent_payload = create_call.args[1]
    assert sent_payload["details"]["pricing"]["pricePerUnit"]["amount"] == pytest.approx(0.00145)
    assert sent_payload["details"]["description"] == "Fast delivery, 24/7 online"  # description offer cũ được giữ nguyên


@pytest.mark.asyncio
async def test_429_then_create_also_fails_reports_clear_error_no_infinite_retry():
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429), FakeResponse(429)]
    client.delete.return_value = FakeResponse(200)
    # Trường hợp hiếm (user nói "không bao giờ" nhưng code vẫn phải xử lý an
    # toàn nếu thực tế có lúc khác): tạo mới cũng lỗi.
    client.post.return_value = FakeResponse(500, text="Internal Server Error")

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is False
    assert link is None
    assert client.post.call_count == 1  # không lặp lại nhiều lần
    assert "500" in message


@pytest.mark.asyncio
async def test_429_but_create_new_disabled_does_not_attempt_recreate():
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429), FakeResponse(429)]

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=False,
    )

    assert success is False
    assert link is None
    client.post.assert_not_called()
    client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_price_and_stock_both_changed_uses_full_update_not_price_only():
    """Bug THẬT gặp trên tài khoản thật 2026-09-13: giá đổi VÀ stock/minQty
    cũng đổi, nhưng code cũ ưu tiên gọi endpoint 'chỉ đổi giá' trước — endpoint
    đó KHÔNG có khả năng đổi stock/minQty. Nó trả về 200 nên hàm dừng lại,
    tưởng đã xong, nhưng stock/minQty CHƯA HỀ được gửi lên Eldorado thật (đã
    xác nhận bằng cách fetch lại offer thật: giá đổi đúng, stock/minQty vẫn
    y hệt giá trị cũ). only_price_changed=False phải bắt buộc dùng endpoint
    update đầy đủ, không được đụng vào endpoint đổi-giá-riêng."""
    client = AsyncMock()
    client.put.return_value = FakeResponse(200)

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 700,
        price_changed=True, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )

    assert success is True
    assert client.put.call_count == 1
    called_url = client.put.call_args_list[0].args[0]
    assert called_url == "https://api/update"  # KHÔNG được là urls.change


@pytest.mark.asyncio
async def test_only_stock_changed_no_price_change_uses_full_update():
    client = AsyncMock()
    client.put.return_value = FakeResponse(200)

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.0016"), 999999, 1500,
        price_changed=False, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )

    assert success is True
    called_url = client.put.call_args_list[0].args[0]
    assert called_url == "https://api/update"


@pytest.mark.asyncio
async def test_non_429_failure_does_not_trigger_recreate():
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(400, text="Bad Request"), FakeResponse(400)]

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is False
    assert link is None
    client.post.assert_not_called()


# ---------------------------------------------------------------------------
# Upload ảnh khi tạo lại offer Item (image_url) — xem writer._upload_main_image
# ---------------------------------------------------------------------------
import writer  # noqa: E402
from image_source import ImageError  # noqa: E402

UPLOADED = {"smallImage": "new_1Small.png", "largeImage": "new_2Large.png", "originalSizeImage": "new_3Original.png"}


def make_item_offer() -> OwnOffer:
    offer = make_offer()
    offer.offer_type = "CustomItem"
    offer.category = "CustomItem"
    offer.raw_offer = {
        "gameId": "g1", "category": "CustomItem", "attributes": [], "volumeDiscounts": [],
        "mainOfferImage": {"smallImage": "old_1Small.png", "largeImage": "old_2Large.png", "originalSizeImage": "old_3Original.png"},
    }
    return offer


def fake_download(monkeypatch, result=None, error=None):
    async def _download(url):
        if error:
            raise error
        return b"\x89PNG\r\n\x1a\n", "image.png", "image/png"

    monkeypatch.setattr(writer, "download_image", _download)


@pytest.mark.asyncio
async def test_item_recreate_with_image_url_uploads_and_sends_new_main_image(monkeypatch):
    fake_download(monkeypatch)
    client = AsyncMock()
    client.upload_offer_image.return_value = UPLOADED
    client.post.return_value = FakeResponse(201, _json={"offer": {"id": "new-item"}})

    link, status = await writer.recreate_offer_from_snapshot(
        client, make_item_offer(), Decimal("1.5"), 10, 1, image_url="https://x.test/a.png"
    )

    assert link == "https://www.eldorado.gg/dashboard/offers/CustomItem/edit/new-item"
    client.upload_offer_image.assert_awaited_once_with(b"\x89PNG\r\n\x1a\n", "image.png", "image/png")
    sent = client.post.call_args.args[1]
    assert sent["details"]["mainOfferImage"] == UPLOADED  # ảnh MỚI, không phải old_*


@pytest.mark.asyncio
async def test_item_recreate_without_image_url_keeps_old_behavior(monkeypatch):
    fake_download(monkeypatch, error=AssertionError("không được tải ảnh khi cột Image URL trống"))
    client = AsyncMock()
    client.post.return_value = FakeResponse(201, _json={"offer": {"id": "new-item"}})

    await writer.recreate_offer_from_snapshot(client, make_item_offer(), Decimal("1.5"), 10, 1)

    client.upload_offer_image.assert_not_called()
    sent = client.post.call_args.args[1]
    assert sent["details"]["mainOfferImage"]["smallImage"] == "old_1Small.png"


@pytest.mark.asyncio
async def test_item_429_recreate_image_download_failure_keeps_old_offer(monkeypatch):
    """Ảnh lỗi -> KHÔNG được xoá offer cũ, KHÔNG được tạo offer mới."""
    fake_download(monkeypatch, error=ImageError("Tải ảnh thất bại (status 404) từ Image URL"))
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429), FakeResponse(429)]

    success, message, link = await apply_update(
        client, make_item_offer(), make_urls(), Decimal("1.5"), 10, 1,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
        image_url="https://x.test/bad.png",
    )

    assert success is False
    assert link is None
    assert "lỗi ảnh" in message and "giữ nguyên" in message
    client.delete.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_item_recreate_eldorado_upload_failure_becomes_image_error(monkeypatch):
    from eldorado_api import EldoradoApiError

    fake_download(monkeypatch)
    client = AsyncMock()
    client.upload_offer_image.side_effect = EldoradoApiError("Upload ảnh lên Eldorado thất bại (status 500)")

    with pytest.raises(ImageError, match="500"):
        await writer.recreate_offer_from_snapshot(
            client, make_item_offer(), Decimal("1.5"), 10, 1, image_url="https://x.test/a.png"
        )
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_non_item_offer_ignores_image_url(monkeypatch):
    fake_download(monkeypatch, error=AssertionError("Currency không được đụng tới ảnh"))
    client = AsyncMock()
    client.post.return_value = FakeResponse(201, _json={"offer": {"id": "new-cur"}})

    link, _ = await writer.recreate_offer_from_snapshot(
        client, make_offer(), Decimal("0.00145"), 1000, 69, image_url="https://x.test/a.png"
    )

    assert link is not None
    client.upload_offer_image.assert_not_called()


@pytest.mark.asyncio
async def test_item_recreate_upload_network_error_becomes_image_error(monkeypatch):
    import httpx

    fake_download(monkeypatch)
    client = AsyncMock()
    client.upload_offer_image.side_effect = httpx.ReadTimeout("timeout")

    with pytest.raises(ImageError, match="lỗi mạng"):
        await writer.recreate_offer_from_snapshot(
            client, make_item_offer(), Decimal("1.5"), 10, 1, image_url="https://x.test/a.png"
        )
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_item_create_uses_item_endpoint_and_augmented_game_payload():
    """Bug thật 2026-09-20: code cũ POST `/me/offers` (404) với gameId/category ở
    cấp ngoài; endpoint thật là `/me/offers/item` đòi Details + AugmentedGame."""
    client = AsyncMock()
    client.post.return_value = FakeResponse(201, _json={"offer": {"id": "new-item"}})

    await writer.recreate_offer_from_snapshot(client, make_item_offer(), Decimal("1.5"), 10, 1)

    url, payload = client.post.call_args.args
    assert url == "https://www.eldorado.gg/api/v1/item-management/me/offers/item"
    assert set(payload) == {"details", "augmentedGame"}
    assert payload["augmentedGame"]["gameId"] == "g1"
    assert payload["augmentedGame"]["category"] == "CustomItem"


@pytest.mark.asyncio
async def test_create_success_without_readable_id_does_not_delete_old_offer():
    """Response 201 nhưng không có id -> KHÔNG được ghép link '.../edit/None' rồi
    xoá offer cũ + ghi đè sheet bằng link rác."""
    client = AsyncMock()
    client.post.return_value = FakeResponse(201, text="{}", _json={})

    link, status = await writer.create_new_offer(client, make_item_offer(), Decimal("1.5"), 10, 1)

    assert link is None
    assert status == 201
    client.delete.assert_not_called()
