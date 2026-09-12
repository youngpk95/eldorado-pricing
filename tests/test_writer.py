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
        price_changed=True, allow_create_new=True, create_new_enabled=True,
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
        price_changed=True, allow_create_new=True, create_new_enabled=True,
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
        price_changed=True, allow_create_new=True, create_new_enabled=True,
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
        price_changed=True, allow_create_new=True, create_new_enabled=False,
    )

    assert success is False
    assert link is None
    client.post.assert_not_called()
    client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_non_429_failure_does_not_trigger_recreate():
    client = AsyncMock()
    client.put.side_effect = [FakeResponse(400, text="Bad Request"), FakeResponse(400)]

    success, message, link = await apply_update(
        client, make_offer(), make_urls(), Decimal("0.00145"), 999999, 69,
        price_changed=True, allow_create_new=True, create_new_enabled=True,
    )

    assert success is False
    assert link is None
    client.post.assert_not_called()
