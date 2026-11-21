"""Test luồng xử lý 429 (quá tải) của writer.apply_update — giả lập response
HTTP, KHÔNG gọi Eldorado thật (không có cách nào an toàn/hợp lệ để cố tình
làm tài khoản thật bị 429). Giả định cũ (2026-09-13) "tạo offer MỚI không bao
giờ bị 429" đã SAI — 2026-11-14 POST tạo offer bị 429 "Quota exceeded" do
tool tạo dồn dập; xem các test giãn cách/tạm dừng tạo ở cuối file."""
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
        raw_offer={"gameId": "220", "category": "Currency", "attributes": [{"id": "path-of-exile-orbs", "type": "Select", "value": {"id": "divine-orb"}}], "volumeDiscounts": []},
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
async def test_currency_429_never_deletes_or_recreates():
    """Test thật 2026-11-14 19:31 (Divine COU): "Quota exceeded" chặn MỌI lệnh
    ghi trừ DELETE -> Currency (xoá trước rồi mới tạo) bị mất trắng offer.
    Giờ Currency gặp 429 thì GIỮ NGUYÊN offer, không xoá, không tạo."""
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429, text="Whoa! Calm down, cowboy!"), FakeResponse(429)]

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is False and link is None
    assert message.startswith("⏳") and "GIỮ NGUYÊN offer" in message
    client.delete.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_item_429_triggers_single_recreate_with_new_price_and_returns_new_link():
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429, text="Whoa! Calm down, cowboy!"), FakeResponse(429)]
    client.delete.return_value = FakeResponse(200)
    client.post.return_value = FakeResponse(201, _json={"offer": {"id": "new-id-456"}})

    success, message, link = await apply_update(
        client, make_item_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is True
    assert link == "https://www.eldorado.gg/dashboard/offers/CustomItem/edit/new-id-456"
    assert client.post.call_count == 1  # đúng 1 lần tạo, không có retry loop
    # Item -> tạo MỚI trước, xoá cũ SAU (offer cũ không bao giờ mất trắng)
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
        client, make_item_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
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


# ---------------------------------------------------------------------------
# Giãn cách + tạm dừng TẠO offer (bug thật 2026-11-14, "Mirror COU")
# ---------------------------------------------------------------------------
QUOTA_BODY = '{"code":429,"messages":["Whoa! Calm down, cowboy! Quota exceeded. Please try again later."]}'


@pytest.mark.asyncio
async def test_create_429_quota_sets_cooldown_and_blocks_next_create_without_deleting():
    import offer_cache
    import writer
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429), FakeResponse(429)]
    client.delete.return_value = FakeResponse(200)
    client.post.return_value = FakeResponse(429, text=QUOTA_BODY)

    success, message, _ = await apply_update(
        client, make_item_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )
    assert success is False
    assert "429" in message and "tạm dừng tạo lại" in message
    assert offer_cache.get_create_cooldown_until(__import__("time").time()) is not None

    # Lần sau (dù đã qua giãn cách) vẫn bị chặn vì hết quota -> KHÔNG xoá, KHÔNG POST.
    offer_cache.set_last_create_at(0.0)
    client.reset_mock()
    client.put.side_effect = [FakeResponse(429), FakeResponse(429)]
    success, message, link = await apply_update(
        client, make_item_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )
    assert success is False and link is None
    assert "offer cũ được giữ nguyên" in message
    client.delete.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_second_create_within_min_interval_is_deferred_without_deleting():
    """Đúng tình huống log: 2 dòng cùng cần tạo lại trong 1 chu kỳ -> chỉ dòng
    đầu được tạo, dòng sau phải chờ (không xoá offer cũ)."""
    import writer
    client = AsyncMock()
    client.delete.return_value = FakeResponse(200)
    client.post.return_value = FakeResponse(201, _json={"id": "new-1"})

    link1, _ = await writer.create_new_offer(client, make_offer(), Decimal("1"), 10, 1)
    assert link1 is not None

    client.reset_mock()
    with pytest.raises(writer.CreateCooldownError):
        await writer.create_new_offer(client, make_offer(), Decimal("1"), 10, 1)
    client.delete.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_create_allowed_again_after_min_interval(monkeypatch):
    import writer
    client = AsyncMock()
    client.post.return_value = FakeResponse(201, _json={"id": "new-1"})
    await writer.recreate_offer_from_snapshot(client, make_offer(), Decimal("1"), 10, 1)
    import offer_cache
    offer_cache.set_last_create_at(offer_cache.get_last_create_at() - writer.config.CREATE_MIN_INTERVAL_SECONDS - 1)
    link, _ = await writer.recreate_offer_from_snapshot(client, make_offer(), Decimal("1"), 10, 1)
    assert link is not None
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_full_update_429_falls_back_to_change_price_without_recreate():
    """User xác nhận 2026-11-14: trang sửa offer (update đầy đủ) bị 429 nhưng ô
    đổi giá ngoài danh sách (changePrice) vẫn được -> phải thử changePrice
    trước, KHÔNG xoá + tạo lại."""
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(429), FakeResponse(200)]

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 5, 69,
        price_changed=True, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )

    assert success is True and link is None
    assert [c.args[0] for c in client.put.call_args_list] == ["https://api/update", "https://api/change"]
    assert "stock/minQty CHƯA cập nhật" in message
    client.delete.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_details_gate_spaces_currency_details_and_still_changes_price():
    """Đo thực tế 2026-11-21: PUT /details Currency có bình quota rất nhỏ -> cả shop
    chỉ 1 lệnh/khoảng giãn. Lệnh đầu đi /details; lệnh kế (chưa tới lượt) chỉ đổi
    giá bằng changePrice, hoặc không gửi gì nếu giá không đổi."""
    import offer_cache
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(200), FakeResponse(200)]

    ok1, _, _ = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 5, 69,
        price_changed=True, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )
    ok2, msg2, _ = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00146"), 5, 69,
        price_changed=True, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )
    ok3, msg3, _ = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00146"), 9, 69,
        price_changed=False, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )

    assert ok1 and ok2 and not ok3
    assert [c.args[0] for c in client.put.call_args_list] == ["https://api/update", "https://api/change"]
    assert "chờ lượt update đầy đủ" in msg2
    assert "Chờ lượt" in msg3


def test_details_gate_429_keeps_same_interval():
    import offer_cache
    assert offer_cache.reserve_details_slot(1000.0, 200) is None
    assert offer_cache.reserve_details_slot(1100.0, 200) == 1200.0      # chưa tới lượt
    assert offer_cache.reserve_details_slot(1200.0, 200) is None        # tới lượt
    # 429 liên tiếp: nhịp luôn là 200s, không lùi gấp đôi
    assert offer_cache.record_details_429(1300.0, 200) == 1500.0
    assert offer_cache.record_details_429(1500.0, 200) == 1700.0
    # Retry-After lớn hơn thì theo Retry-After; nhỏ hơn thì vẫn giữ 200s
    assert offer_cache.record_details_429(2000.0, 200, 600.0) == 2600.0
    assert offer_cache.record_details_429(3000.0, 200, 30.0) == 3200.0
    assert offer_cache.reserve_details_slot(0.0, 0) is None             # interval=0 = tắt

def test_create_backoff_grows_exponentially_and_caps():
    import offer_cache
    waits = [offer_cache.record_create_429(1000.0, 60, 900, None, "q") - 1000.0 for _ in range(6)]
    assert waits == [60, 120, 240, 480, 900, 900]


def test_create_backoff_uses_retry_after_when_given():
    import offer_cache
    assert offer_cache.record_create_429(1000.0, 60, 900, 7.0, "q") == 1007.0


@pytest.mark.asyncio
async def test_create_success_clears_backoff_immediately():
    import time
    import offer_cache
    import writer
    offer_cache.record_create_429(time.time() - 1000, 60, 900, None, "q")
    offer_cache.record_create_429(time.time() - 1000, 60, 900, None, "q")
    client = AsyncMock()
    client.post.return_value = FakeResponse(201, _json={"id": "new-1"})
    link, _ = await writer.recreate_offer_from_snapshot(client, make_offer(), Decimal("1"), 10, 1)
    assert link is not None
    # Lần 429 kế tiếp phải bắt đầu lại từ mức chờ ngắn nhất, không cộng dồn.
    assert offer_cache.record_create_429(0.0, 60, 900, None, "q") == 60.0


@pytest.mark.asyncio
async def test_first_create_429_waits_only_base_seconds():
    import time
    import offer_cache
    import writer
    client = AsyncMock()
    client.post.return_value = FakeResponse(429, text="Too Many Requests")
    before = time.time()
    await writer.recreate_offer_from_snapshot(client, make_offer(), Decimal("1"), 10, 1)
    until = offer_cache.get_create_cooldown_until(time.time())
    assert until is not None
    assert until - before <= writer.config.CREATE_BACKOFF_BASE_SECONDS + 1


@pytest.mark.asyncio
async def test_image_error_before_post_releases_create_slot(monkeypatch):
    """Lỗi ảnh xảy ra TRƯỚC khi gửi POST -> không được chiếm lượt tạo của dòng khác."""
    import offer_cache
    import writer
    from image_source import ImageError

    async def boom(*a, **k):
        raise ImageError("ảnh hỏng")
    monkeypatch.setattr(writer, "_upload_main_image", boom)
    item = make_offer()
    item = OwnOffer(**{**item.__dict__, "offer_type": "CustomItem", "category": "CustomItem"})

    client = AsyncMock()
    with pytest.raises(ImageError):
        await writer.recreate_offer_from_snapshot(client, item, Decimal("1"), 10, 1, image_url="https://x/y.png")
    assert offer_cache.get_last_create_at() == 0.0

    client.post.return_value = FakeResponse(201, _json={"id": "new-2"})
    link, _ = await writer.recreate_offer_from_snapshot(client, make_offer(), Decimal("1"), 10, 1)
    assert link is not None


@pytest.mark.asyncio
async def test_currency_recreate_waits_between_delete_and_create(monkeypatch):
    """User yêu cầu 2026-11-14: giãn ~5s giữa xoá offer cũ và tạo offer mới."""
    import writer
    monkeypatch.setattr(writer.config, "RECREATE_STEP_DELAY_SECONDS", 5)
    events = []
    client = AsyncMock()

    async def fake_delete(url):
        events.append("delete")
        return FakeResponse(200)

    async def fake_post(url, payload):
        events.append("post")
        return FakeResponse(201, _json={"id": "new-1"})

    async def fake_sleep(sec):
        events.append(f"sleep{sec:g}")

    client.delete.side_effect = fake_delete
    client.post.side_effect = fake_post
    monkeypatch.setattr(writer.asyncio, "sleep", fake_sleep)

    link, _ = await writer.create_new_offer(client, make_offer(), Decimal("1"), 10, 1)
    assert link is not None
    assert events == ["delete", "sleep5", "post"]



@pytest.mark.asyncio
async def test_currency_without_attributes_sends_no_full_update_but_price_only_still_works():
    offer = make_offer()
    offer.raw_offer["attributes"] = []
    client = AsyncMock()
    client.put.return_value = FakeResponse(200)

    ok, msg, _ = await apply_update(
        client, offer, make_urls(), Decimal("0.00145"), 999999, 700,
        price_changed=True, only_price_changed=False, allow_create_new=True, create_new_enabled=True,
    )
    assert ok is False and "attribute" in msg
    client.put.assert_not_called()

    ok, _, _ = await apply_update(
        client, offer, make_urls(), Decimal("0.00145"), offer.quantity, offer.min_quantity,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_no_attributes_price_only_failure_does_not_fall_back_to_full_put_or_delete():
    offer = make_offer()
    offer.raw_offer["attributes"] = []
    client = AsyncMock()
    client.put.return_value = FakeResponse(429)

    ok, msg, link = await apply_update(
        client, offer, make_urls(), Decimal("0.00145"), offer.quantity, offer.min_quantity,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )
    assert ok is False and link is None and client.put.call_count == 1
    client.delete.assert_not_called()
    client.post.assert_not_called()


def test_build_offer_attributes_uses_web_format_values_string_list():
    """2026-11-18: PUT /details chỉ nhận Select dạng {id,type,values:[<id chuỗi>]} (đúng như web
    Eldorado); dạng cũ value:"divine-orb" bị 400 'Required attribute Orbs is not set'."""
    from writer import _build_offer_attributes
    live = [{"values": [{"name": "Divine Orb", "id": "divine-orb"}], "name": "Orbs",
             "id": "path-of-exile-2-orbs", "type": "Select"}]
    assert _build_offer_attributes(live) == [{"id": "path-of-exile-2-orbs", "type": "Select", "values": ["divine-orb"]}]
    old = [{"value": {"id": "divine-orb"}, "id": "o", "type": "Select"}]  # snapshot dạng cũ chỉ có `value`
    assert _build_offer_attributes(old) == [{"id": "o", "type": "Select", "values": ["divine-orb"]}]
    two = [{"id": "x", "type": "Select", "values": [{"id": "a"}, {"id": "b"}]}]
    assert _build_offer_attributes(two) == [{"id": "x", "type": "Select", "values": ["a", "b"]}]
    assert _build_offer_attributes([{"id": "x", "type": "Select", "values": []}]) == []
    assert _build_offer_attributes([{"id": "n", "type": "Numeric", "value": 5}]) == [{"id": "n", "type": "Numeric", "value": 5}]