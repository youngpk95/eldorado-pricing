"""Test 2026-11-16: (1) tạm dừng MỌI lệnh ghi sau khi 1 lệnh bị 429 — không
gửi tiếp lệnh vô ích; (2) đăng nhập bằng Seller API chính thức (Bearer token)
+ tự đăng nhập lại khi Eldorado trả 401."""
import sys
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eldo_auth  # noqa: E402
import offer_cache  # noqa: E402
import writer  # noqa: E402
from eldorado_api import EldoradoClient  # noqa: E402
from models import OfferUrls  # noqa: E402
from test_writer import FakeResponse, make_offer  # noqa: E402

URLS = OfferUrls(detail="https://api/detail", compare="", update="https://api/update", change="https://api/change")


# ---------------------------------------------------------------- (1) tạm dừng ghi

def test_write_pause_backoff_grows_only_after_pause_expires():
    # 429 lần đầu: chờ BASE.
    assert offer_cache.record_write_429(1000.0, 60, 900, None, "q") == 1060.0
    # Dòng khác cũng 429 TRONG lúc đang dừng -> KHÔNG cộng dồn.
    assert offer_cache.record_write_429(1010.0, 60, 900, None, "q") == 1060.0
    # Hết dừng, lần thử tiếp vẫn 429 -> gấp đôi.
    assert offer_cache.record_write_429(1100.0, 60, 900, None, "q") == 1220.0


def test_write_pause_respects_retry_after_and_cap():
    assert offer_cache.record_write_429(0.0, 60, 900, 30.0, "q") == 30.0
    for t in range(1, 10):
        until = offer_cache.record_write_429(t * 10_000.0, 60, 900, None, "q")
    assert until - 90_000.0 == 900


def test_write_success_clears_pause():
    offer_cache.record_write_429(time.time(), 60, 900, None, "q")
    assert offer_cache.get_write_pause_until(time.time()) is not None
    offer_cache.record_write_success()
    assert offer_cache.get_write_pause_until(time.time()) is None
    assert offer_cache.record_write_429(0.0, 60, 900, None, "q") == 60.0


@pytest.mark.asyncio
async def test_apply_update_sends_nothing_while_paused():
    offer_cache.record_write_429(time.time(), 600, 900, None, "Quota exceeded")
    client = AsyncMock()
    success, message, link = await writer.apply_update(
        client, make_offer(), URLS, Decimal("1"), 10, 1,
        price_changed=True, only_price_changed=True, allow_create_new=True, create_new_enabled=True,
    )
    assert success is False and link is None
    assert message.startswith("⏳")
    client.put.assert_not_called()
    client.post.assert_not_called()
    client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_recreate_from_snapshot_blocked_while_paused():
    """Luồng 404 tự tạo lại cũng không được POST khi đang tạm dừng ghi."""
    offer_cache.record_write_429(time.time(), 600, 900, None, "Quota exceeded")
    client = AsyncMock()
    with pytest.raises(writer.CreateCooldownError):
        await writer.recreate_offer_from_snapshot(client, make_offer(), Decimal("1"), 10, 1)
    client.post.assert_not_called()
    client.delete.assert_not_called()


# ---------------------------------------------------------------- client thật (httpx mock)

class FakeAuth:
    def __init__(self) -> None:
        self.calls = 0
        self.invalidated = 0

    async def get_auth_headers(self):
        self.calls += 1
        return {"Authorization": f"Bearer tok{self.invalidated}"}

    def invalidate(self, stale_headers=None):
        self.invalidated += 1


def _client_with(handler, auth=None) -> EldoradoClient:
    client = EldoradoClient(auth or FakeAuth())
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
async def test_put_429_starts_pause_and_200_clears_it(monkeypatch):
    monkeypatch.setattr(writer.config, "ELDORADO_WRITE_DELAY_MS", 0)
    statuses = iter([429, 200])
    client = _client_with(lambda req: httpx.Response(next(statuses), text="Quota exceeded"))
    await client.post("https://www.eldorado.gg/api/predefinedOffers/", {"x": 1})
    assert offer_cache.get_write_pause_until(time.time()) is not None
    await client.post("https://www.eldorado.gg/api/predefinedOffers/", {"x": 1})
    assert offer_cache.get_write_pause_until(time.time()) is None
    await client.aclose()


@pytest.mark.asyncio
async def test_change_price_429_does_not_pause_other_offers(monkeypatch):
    """Hạn mức changePrice tính RIÊNG TỪNG OFFER (log 2026-11-22) -> 429 ở 1 offer
    không được dừng ghi của cả shop."""
    monkeypatch.setattr(writer.config, "ELDORADO_WRITE_DELAY_MS", 0)
    client = _client_with(lambda req: httpx.Response(429, text="Quota exceeded"))
    await client.put("https://www.eldorado.gg/api/predefinedOffersUser/me/x/changePrice", {"amount": 1})
    assert offer_cache.get_write_pause_until(time.time()) is None
    await client.aclose()


@pytest.mark.asyncio
async def test_details_429_alone_does_not_pause(monkeypatch):
    """14/11: có lúc CHỈ PUT details bị 429 còn changePrice vẫn chạy -> không
    được dừng ghi cả shop chỉ vì details 429."""
    monkeypatch.setattr(writer.config, "ELDORADO_WRITE_DELAY_MS", 0)
    client = _client_with(lambda req: httpx.Response(429, text="Calm down"))
    await client.put("https://www.eldorado.gg/api/predefinedOffers/x/details", {"a": 1})
    assert offer_cache.get_write_pause_until(time.time()) is None
    await client.post("https://www.eldorado.gg/api/predefinedOffers/", {"a": 1})
    assert offer_cache.get_write_pause_until(time.time()) is not None
    await client.aclose()


def test_concurrent_429s_with_retry_after_do_not_stack():
    assert offer_cache.record_write_429(1000.0, 60, 900, 30.0, "q") == 1030.0
    for t in (1001.0, 1002.0, 1003.0):
        assert offer_cache.record_write_429(t, 60, 900, 30.0, "q") == 1030.0
    # Hết dừng, 429 không có Retry-After -> chỉ mức thứ 2 (120s), không phải 15 phút.
    assert offer_cache.record_write_429(2000.0, 60, 900, None, "q") == 2120.0


@pytest.mark.asyncio
async def test_delete_does_not_clear_pause(monkeypatch):
    """DELETE vẫn chạy khi 'Quota exceeded' -> 204 không có nghĩa hết bị chặn."""
    monkeypatch.setattr(writer.config, "ELDORADO_WRITE_DELAY_MS", 0)
    offer_cache.record_write_429(time.time(), 600, 900, None, "q")
    client = _client_with(lambda req: httpx.Response(204))
    await client.delete("https://api/del")
    assert offer_cache.get_write_pause_until(time.time()) is not None
    await client.aclose()


@pytest.mark.asyncio
async def test_401_reauthenticates_and_retries_once():
    seen = []

    def handler(req):
        seen.append(req.headers["Authorization"])
        return httpx.Response(401 if len(seen) == 1 else 200, json={"results": []})

    auth = FakeAuth()
    client = _client_with(handler, auth)
    result = await client.get_competitors("https://api/compare")
    assert result == []
    assert seen == ["Bearer tok0", "Bearer tok1"]
    assert auth.invalidated == 1
    await client.aclose()


# ---------------------------------------------------------------- (2) Seller API auth

@pytest.mark.asyncio
async def test_seller_auth_caches_token_and_refreshes_before_expiry(monkeypatch):
    auth = eldo_auth.SellerApiAuth("id", "secret", "UA")
    fetched = []

    async def fake_fetch():
        fetched.append(1)
        return f"t{len(fetched)}", 900.0
    monkeypatch.setattr(auth, "_fetch_token", fake_fetch)

    now = [1000.0]
    monkeypatch.setattr(eldo_auth.time, "time", lambda: now[0])
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t1"}
    now[0] += 700  # còn 200s > biên 120s -> dùng lại
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t1"}
    now[0] += 100  # còn 100s < 120s -> xin token mới
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t2"}
    # 401 của request dùng token CŨ (t1) -> đã có t2 rồi, không xin lại.
    auth.invalidate({"Authorization": "Bearer t1"})
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t2"}
    auth.invalidate({"Authorization": "Bearer t2"})
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t3"}


@pytest.mark.asyncio
async def test_seller_auth_short_lived_token_not_refetched_every_request(monkeypatch):
    auth = eldo_auth.SellerApiAuth("id", "secret", "UA")
    fetched = []

    async def fake_fetch():
        fetched.append(1)
        return "t", 60.0
    monkeypatch.setattr(auth, "_fetch_token", fake_fetch)
    now = [1000.0]
    monkeypatch.setattr(eldo_auth.time, "time", lambda: now[0])
    await auth.get_auth_headers()
    now[0] += 10
    await auth.get_auth_headers()
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_seller_auth_failure_is_not_retried_for_every_row(monkeypatch):
    from eldorado_api import EldoradoApiError
    auth = eldo_auth.SellerApiAuth("id", "secret", "UA")
    calls = []

    async def failing_fetch():
        calls.append(1)
        raise EldoradoApiError("Lấy token Seller API thất bại (status 401)")
    monkeypatch.setattr(auth, "_fetch_token", failing_fetch)
    now = [1000.0]
    monkeypatch.setattr(eldo_auth.time, "time", lambda: now[0])
    for _ in range(5):
        with pytest.raises(EldoradoApiError, match="status 401"):
            await auth.get_auth_headers()
    assert len(calls) == 1
    now[0] += eldo_auth.SELLER_TOKEN_FAILURE_RETRY_SECONDS + 1
    with pytest.raises(EldoradoApiError):
        await auth.get_auth_headers()
    assert len(calls) == 2


def test_build_auth_picks_seller_api_when_configured(monkeypatch):
    monkeypatch.setattr(eldo_auth.config, "USE_SELLER_API", True)
    monkeypatch.setattr(eldo_auth.config, "ELDORADO_SELLER_CLIENT_ID", "id")
    monkeypatch.setattr(eldo_auth.config, "ELDORADO_SELLER_CLIENT_SECRET", "secret")
    assert isinstance(eldo_auth.build_auth("UA"), eldo_auth.SellerApiAuth)
    monkeypatch.setattr(eldo_auth.config, "USE_SELLER_API", False)
    assert isinstance(eldo_auth.build_auth("UA"), eldo_auth.EldoradoAuth)


@pytest.mark.asyncio
async def test_seller_auth_early_refresh_failure_keeps_using_valid_token(monkeypatch):
    """Làm mới sớm (còn <120s) lỗi nhưng token cũ chưa hết hạn -> vẫn trả token cũ, không từ chối request."""
    from eldorado_api import EldoradoApiError
    auth = eldo_auth.SellerApiAuth("id", "secret", "UA")
    state = {"fail": False, "calls": 0}

    async def fetch():
        state["calls"] += 1
        if state["fail"]:
            raise EldoradoApiError("Lấy token Seller API thất bại (status 500)")
        return "t1", 900.0
    monkeypatch.setattr(auth, "_fetch_token", fetch)
    now = [1000.0]
    monkeypatch.setattr(eldo_auth.time, "time", lambda: now[0])
    await auth.get_auth_headers()
    state["fail"] = True
    now[0] += 800  # trong biên làm mới, token còn 100s
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t1"}
    assert await auth.get_auth_headers() == {"Authorization": "Bearer t1"}  # trong thời gian chờ thử lại
    assert state["calls"] == 2
    now[0] += 200  # token hết hạn thật, vẫn trong thời gian chờ thử lại -> lỗi
    with pytest.raises(EldoradoApiError):
        await auth.get_auth_headers()
