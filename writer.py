"""Ghi thay đổi lên Eldorado thật — port từ req_Eldo.py (put_eldo/create_new),
dùng EldoradoClient (httpx.AsyncClient) thay requests đồng bộ.

Đơn giản hoá có chủ đích so với bản gốc: bản gốc có 2 nhánh gần như trùng
lặp trong checkupdate.py (thử URL_UPDATE trước rồi mới URL_CHANGE, HOẶC
ngược lại, tuỳ field nào đổi) — gộp thành 1 hàm duy nhất `apply_update()`:
nếu giá đổi thì thử endpoint đổi giá (nhẹ, nhanh) trước, fallback sang
endpoint update đầy đủ; nếu giá KHÔNG đổi (chỉ stock/thời gian/minQty) thì chỉ
cần endpoint update đầy đủ.

Khi gặp 429 (quá tải), fallback sang xoá + tạo lại offer VỚI GIÁ MỚI luôn
(không phải tạo lại giá cũ rồi đổi giá sau). Giả định cũ (2026-09-13) "tạo
offer MỚI không bao giờ bị 429" đã SAI: 2026-11-14 POST tạo offer trả 429
"Whoa! Calm down, cowboy! Quota exceeded" do tool tạo dồn dập. Khi đó tool
chờ tăng dần (60s, 2, 4, 8 phút... tối đa 15 phút, hoặc theo Retry-After —
offer_cache.record_create_429), tạo được 1 lần là hết chờ ngay; trong lúc
chờ KHÔNG xoá/tạo lại offer nào; ngoài ra mọi lần tạo cách nhau tối
thiểu CREATE_MIN_INTERVAL_SECONDS — xem _reserve_create_slot. Vẫn
không retry ngay trong cùng chu kỳ. Nếu chu kỳ SAU vẫn gặp 429 khi đổi giá (vì offer vừa tạo lại cũng cũ
đi theo thời gian), `product_pipeline.py` sẽ tự lặp lại đúng luồng này —
xoá + tạo lại lần nữa với giá mới nhất tại thời điểm đó. Vì offer mới có ID
khác hẳn, sheet phải tự cập nhật lại `My Listing URL` (cột F) sang link mới
— xem `product_pipeline.py`, nếu không chu kỳ sau sẽ tìm nhầm offer đã bị
xoá."""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import datetime
from decimal import Decimal

import httpx

import config
import offer_cache
from eldorado_api import EldoradoApiError, EldoradoClient, retry_after_seconds
from image_source import ImageError, download_image
from models import OfferUrls, OwnOffer, attribute_value_id

logger = logging.getLogger(__name__)

READ_ONLY_FIELDS = [
    "id", "userId", "expireDate", "offerVersion",
    "pricePerUnitWithDiscount", "discountPercentage",
    "pricePerUnitInUSD", "exchangeRate",
    "attributes", "tradeEnvironmentValues", "offerAttributeIdValues",
]


def _build_offer_attributes(raw_attributes: list[dict]) -> list[dict]:
    result = []
    for attr in raw_attributes:
        value_id = attribute_value_id(attr)
        if "id" in attr and value_id is not None:
            result.append({"id": attr["id"], "type": attr.get("type", "Select"), "value": value_id})
    return result


def _build_details_payload(offer: OwnOffer, new_price: Decimal, new_qty: int, new_min_qty: int) -> dict:
    details = copy.deepcopy(offer.raw_offer)
    for field in READ_ONLY_FIELDS:
        details.pop(field, None)
    details["pricing"] = {
        "quantity": new_qty,
        "minQuantity": new_min_qty,
        "pricePerUnit": {"amount": float(new_price), "currency": "USD"},
        "volumeDiscounts": offer.raw_offer.get("volumeDiscounts", []),
    }
    details["guaranteedDeliveryTime"] = offer.guaranteed_delivery_time
    details["description"] = offer.description
    details["deliveryMethod"] = offer.delivery_method

    trade_env_id = offer.trade_environment_values[-1]["id"] if offer.trade_environment_values else None
    return {
        "details": details,
        "augmentedGame": {
            "gameId": offer.raw_offer.get("gameId"),
            "category": offer.category,
            "tradeEnvironmentId": trade_env_id,
            "offerAttributes": _build_offer_attributes(offer.raw_offer.get("attributes", [])),
            "attributeIdsCsv": None,
        },
    }


async def apply_update(
    client: EldoradoClient,
    offer: OwnOffer,
    urls: OfferUrls,
    new_price: Decimal,
    new_qty: int,
    new_min_qty: int,
    price_changed: bool,
    only_price_changed: bool,
    allow_create_new: bool,
    create_new_enabled: bool,
    image_url: str | None = None,
) -> tuple[bool, str, str | None]:
    """Trả về (thành công, mô tả, link offer mới nếu vừa tạo lại).

    `only_price_changed`: True CHỈ khi price_changed=True VÀ không có gì
    khác (stock/minQty) cần đổi. QUAN TRỌNG (bug thật đã gặp 2026-09-13):
    endpoint đổi giá riêng (`urls.change`) CHỈ đổi được giá — nếu vẫn ưu
    tiên gọi endpoint này khi stock/minQty cũng cần đổi, và nó trả về 200,
    hàm sẽ dừng lại tưởng đã xong trong khi stock/minQty chưa hề được gửi
    lên Eldorado. Nên CHỈ được dùng đường tắt (đổi giá riêng, nhẹ hơn) khi
    thực sự không có gì khác cần đổi cùng lúc."""
    # Vừa có lệnh ghi khác bị 429 -> KHÔNG gửi gì cả (2026-11-16): trước đây
    # dòng nào/chu kỳ nào cũng gửi 1-2 lệnh rồi lại 429, phí lệnh vô ích.
    paused_until = offer_cache.get_write_pause_until(time.time())
    if paused_until is not None:
        return False, (
            f"⏳ Eldorado đang chặn ghi (429) — tool tạm dừng MỌI lệnh ghi tới {_hhmm(paused_until)}, "
            "chưa đổi giá/stock dòng này, sẽ tự thử lại sau mốc đó"
        ), None
    no_attrs = lacks_required_attributes(offer)
    if no_attrs and not only_price_changed:
        return False, MISSING_ATTR_NOTE, None
    details_payload = _build_details_payload(offer, new_price, new_qty, new_min_qty)
    price_only_payload = {"amount": float(new_price), "currency": "USD"}

    responses: list[httpx.Response] = []

    if only_price_changed:
        resp_price = await client.put(urls.change, price_only_payload)
        responses.append(resp_price)
        if resp_price.status_code == 200:
            return True, "Đổi giá thành công (change price)", None
        if no_attrs:  # fallback PUT /details sẽ chắc chắn 400 vì thiếu attribute
            return False, f"Đổi giá thất bại (status {resp_price.status_code}) — {MISSING_ATTR_NOTE}", None
        resp_full = await client.put(urls.update, details_payload)
        responses.append(resp_full)
        if resp_full.status_code == 200:
            return True, "Cập nhật thành công (fallback: update đầy đủ)", None
    else:
        # Giá VÀ/HOẶC stock/minQty đều cần đổi -> PHẢI dùng endpoint update
        # đầy đủ (gửi cả 3 field 1 lần) — không dùng đường tắt đổi-giá-riêng
        # ở đây, tránh bỏ sót stock/minQty như bug đã gặp.
        resp_full = await client.put(urls.update, details_payload)
        responses.append(resp_full)
        if resp_full.status_code == 200:
            return True, "Cập nhật thành công (đầy đủ: giá/stock/minQty)", None
        # User xác nhận 2026-11-14: khi trang sửa offer (update đầy đủ) bị 429
        # "Calm down", ô đổi giá ở danh sách offer (changePrice — đúng endpoint
        # urls.change) vẫn đổi được. Nên ưu tiên đổi GIÁ trước (quan trọng nhất)
        # thay vì nhảy thẳng sang xoá + tạo lại; stock/minQty để chu kỳ sau.
        if resp_full.status_code == 429 and price_changed:
            resp_price = await client.put(urls.change, price_only_payload)
            responses.append(resp_price)
            if resp_price.status_code == 200:
                return True, (
                    "Đổi giá thành công (change price) — stock/minQty CHƯA cập nhật vì update đầy đủ "
                    "bị 429, chu kỳ sau thử lại"
                ), None

    is_429 = any(r.status_code == 429 for r in responses)
    status_summary = ", ".join(f"{r.status_code}" for r in responses)
    if not (is_429 and allow_create_new and create_new_enabled):
        body_texts = "; ".join(r.text[:200] for r in responses)
        return False, f"Cập nhật thất bại (status: {status_summary}) — {body_texts}", None

    # Currency/TopUp/GiftCard: KHÔNG BAO GIỜ xoá + tạo lại khi 429. Test thật
    # 2026-11-14 19:31 (Divine COU): khi Eldorado báo "Quota exceeded", MỌI
    # lệnh ghi (details, changePrice, POST tạo) đều 429 — chỉ DELETE vẫn chạy.
    # Loại này phải xoá TRƯỚC rồi mới tạo (create_new_offer) -> xoá xong POST
    # 429 -> mất trắng offer đang bán. Giữ nguyên offer, chu kỳ sau thử lại.
    otype = offer.offer_type or offer.category
    if otype in DELETE_BEFORE_CREATE_TYPES:
        return False, (
            f"⏳ 429 khi đổi giá ({status_summary}) — Eldorado đang chặn ghi, GIỮ NGUYÊN offer "
            "(không xoá + tạo lại vì Currency sẽ mất offer), chu kỳ sau thử lại"
        ), None

    # Item/Account: tạo MỚI trước rồi mới xoá cũ -> POST lỗi thì offer cũ còn
    # nguyên. 429 -> tạo lại VỚI GIÁ MỚI, đúng 1 lần thử — không retry loop.
    try:
        new_link, create_status = await create_new_offer(
            client, offer, new_price, new_qty, new_min_qty, image_url=image_url
        )
    except CreateCooldownError as e:
        # Chặn TRƯỚC khi xoá gì -> offer cũ còn nguyên, chu kỳ sau thử đổi giá lại.
        return False, f"⏳ 429 khi đổi giá — KHÔNG xoá + tạo lại vì {e} (offer cũ được giữ nguyên)", None
    except ImageError as e:
        # Lỗi ảnh xảy ra TRƯỚC khi xoá bất cứ gì (xem create_new_offer) -> offer
        # cũ còn nguyên, chỉ là chưa đổi được giá/stock do 429.
        return False, f"429 rate-limit -> KHÔNG tạo lại offer vì lỗi ảnh: {e} (offer cũ được giữ nguyên)", None
    if new_link:
        return True, "429 rate-limit -> xoá + tạo offer mới thành công", new_link
    return False, f"429 rate-limit -> tạo offer mới cũng thất bại (status {create_status}){create_failure_hint(create_status)}", None


# Loại offer mà Eldorado bắt xoá offer cũ TRƯỚC rồi mới tạo được offer mới
# (giữ đúng như bản gốc RequestEldo.create_new).
DELETE_BEFORE_CREATE_TYPES = ("Currency", "TopUp", "GiftCard")

def lacks_required_attributes(offer: OwnOffer) -> bool:
    """Chỉ Currency (loại đã bị 400 "Required attribute 'Orbs'"); TopUp/GiftCard
    giữ hành vi cũ vì có thể hợp lệ khi không có attribute."""
    return (offer.offer_type or offer.category) == "Currency" and not _build_offer_attributes(
        offer.raw_offer.get("attributes", [])
    )


MISSING_ATTR_NOTE = (
    "❌ Offer không có attribute (vd Orbs) và tool không khôi phục được — KHÔNG gửi lệnh ghi vì chắc chắn bị 400. "
    "Sửa: thêm &attribute_value_id=<id> (vd divine-orb) vào cuối Compare URL của dòng này."
)


class MissingAttributesError(Exception):
    """Offer Currency/TopUp/GiftCard không có attribute nào (vd Orbs) để gửi —
    Eldorado chắc chắn trả 400 "Required attribute ... is not set", nên KHÔNG
    gửi (tránh spam lệnh ghi hỏng mỗi chu kỳ)."""


class CreateCooldownError(Exception):
    """Chưa được phép TẠO offer lúc này (Eldorado báo hết quota, hoặc chưa đủ
    giãn cách giữa 2 lần tạo) — ném ra TRƯỚC khi xoá/upload/POST bất cứ gì,
    để người gọi giữ nguyên offer cũ và để chu kỳ sau thử lại."""

    def __init__(self, until: float, reason: str) -> None:
        self.until = until
        super().__init__(f"{reason}, tool tự thử lại sau {_hhmm(until)}")


def _hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


# Giãn cách giữa 2 lần TẠO offer, dùng chung MỌI dòng (mốc lưu file, xem
# offer_cache.get_last_create_at). Bug thật 2026-11-14: 2 dòng xử lý song song
# POST tạo offer cách nhau 0.6s, chu kỳ nào cũng lặp lại -> Eldorado trả 429
# "Quota exceeded". Lock để 2 dòng chạy song song (asyncio.gather) không cùng
# lọt qua bước kiểm tra; tạo lại lock nếu event loop đổi (asyncio.Lock gắn
# với loop đầu tiên phải chờ nó).
_create_lock: asyncio.Lock | None = None
_create_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_create_lock() -> asyncio.Lock:
    global _create_lock, _create_lock_loop
    loop = asyncio.get_running_loop()
    if _create_lock is None or _create_lock_loop is not loop:
        _create_lock, _create_lock_loop = asyncio.Lock(), loop
    return _create_lock


async def _reserve_create_slot() -> float:
    """Giữ chỗ cho 1 lần tạo offer: kiểm tra (1) đang bị chặn do 429 khi tạo,
    (2) lần tạo gần nhất chưa đủ CREATE_MIN_INTERVAL_SECONDS. Hợp lệ thì ghi
    nhận NGAY mốc tạo rồi mới cho đi tiếp. Trả về mốc CŨ để _release_create_slot
    trả lại lượt nếu rốt cuộc không gửi POST (lỗi ảnh, category lạ)."""
    async with _get_create_lock():
        now = time.time()
        until = offer_cache.get_create_cooldown_until(now)
        if until is not None:
            raise CreateCooldownError(until, "Eldorado đang chặn tạo offer (429)")
        # Lệnh ghi nào đó vừa bị 429 -> tạo offer gần như chắc chắn cũng 429
        # (test thật 2026-11-14: "Quota exceeded" chặn MỌI lệnh ghi) -> không thử.
        paused = offer_cache.get_write_pause_until(now)
        if paused is not None:
            raise CreateCooldownError(paused, "Eldorado đang chặn ghi (429)")
        previous = offer_cache.get_last_create_at()
        next_allowed = previous + config.CREATE_MIN_INTERVAL_SECONDS
        if now < next_allowed:
            raise CreateCooldownError(next_allowed, "vừa tạo offer khác chưa lâu (giãn cách để tránh bị Eldorado chặn)")
        offer_cache.set_last_create_at(now)
        return previous


def _release_create_slot(previous: float) -> None:
    offer_cache.set_last_create_at(previous)


def create_failure_hint(status: int | None) -> str:
    """Đuôi giải thích thêm cho Status khi tạo offer thất bại — 429 nghĩa là
    hết quota tạo offer (đã bật tạm dừng ở _post_new_offer)."""
    if status != 429:
        return ""
    until = offer_cache.get_create_cooldown_until(time.time())
    when = f" tới {_hhmm(until)}" if until else ""
    return f" — Eldorado chặn tạo offer (429), tool tạm dừng tạo lại{when}"


def _delete_url_for_category(otype: str, offer_id: str) -> str:
    if otype in ("Currency", "TopUp", "GiftCard"):
        return f"https://www.eldorado.gg/api/predefinedOffersUser/me/{offer_id}"
    if otype == "CustomItem":
        return f"https://www.eldorado.gg/api/v1/item-management/me/offers/{offer_id}"
    if otype == "Account":
        return f"https://www.eldorado.gg/api/flexibleOffersUser/me/{offer_id}"
    return f"https://www.eldorado.gg/api/predefinedOffersUser/me/{offer_id}"


async def create_new_offer(
    client: EldoradoClient,
    offer: OwnOffer,
    new_price: Decimal,
    new_qty: int,
    new_min_qty: int,
    image_url: str | None = None,
) -> tuple[str | None, int | None]:
    """Xoá offer cũ + tạo offer mới hoàn toàn VỚI GIÁ MỚI luôn — Eldorado's
    429 rate-limit workaround, port từ RequestEldo.create_new. Thứ tự xoá
    trước/sau tuỳ category, giữ đúng như bản gốc. Trả về (link offer mới
    hoặc None, status code của chính request tạo — None nếu lỗi xảy ra
    trước khi kịp gọi request)."""
    # Đang bị chặn tạo offer -> dừng NGAY, trước khi xoá offer cũ (Currency
    # xoá trước rồi mới tạo — POST chắc chắn 429 thì sẽ mất trắng offer).
    if lacks_required_attributes(offer):  # kiểm TRƯỚC khi xoá offer cũ (Currency xoá trước rồi mới tạo)
        raise MissingAttributesError(MISSING_ATTR_NOTE)
    previous_slot = await _reserve_create_slot()
    otype = offer.offer_type or offer.category
    url_del = _delete_url_for_category(otype, offer.offer_id)
    should_delete_before = otype in DELETE_BEFORE_CREATE_TYPES

    if should_delete_before:
        try:
            resp = await client.delete(url_del)
            if resp.status_code not in (200, 204):
                logger.warning("[create_new] Xoá offer cũ trước khi tạo mới thất bại: %s", resp.status_code)
        except Exception as e:
            logger.error("[create_new] Lỗi xoá offer cũ trước: %s", e)
        # Giãn giữa XOÁ và TẠO như thao tác tay (user yêu cầu 2026-11-14).
        await asyncio.sleep(config.RECREATE_STEP_DELAY_SECONDS)

    new_url, status = await _post_with_slot(previous_slot, client, offer, new_price, new_qty, new_min_qty, image_url)

    if new_url and not should_delete_before:
        await asyncio.sleep(config.RECREATE_STEP_DELAY_SECONDS)
        try:
            resp_del = await client.delete(url_del)
            if resp_del.status_code not in (200, 204):
                logger.warning("[create_new] Xoá offer cũ SAU khi tạo mới thất bại: %s", resp_del.status_code)
        except Exception as e:
            logger.error("[create_new] Lỗi xoá offer cũ sau: %s", e)

    return new_url, status


async def recreate_offer_from_snapshot(
    client: EldoradoClient,
    offer: OwnOffer,
    new_price: Decimal,
    new_qty: int,
    new_min_qty: int,
    image_url: str | None = None,
) -> tuple[str | None, int | None]:
    """Tạo offer mới TỪ SNAPSHOT đã lưu (offer_cache.py) — dùng khi phát
    hiện offer gốc đã 404 ở 1 CHU KỲ SAU (không phải do chính lần chạy này
    xoá — xem product_pipeline.py._recover_missing_offer). KHÁC
    `create_new_offer` ở trên: KHÔNG gọi delete, vì offer gốc đã mất từ
    trước, không còn gì để xoá thêm."""
    previous_slot = await _reserve_create_slot()
    return await _post_with_slot(previous_slot, client, offer, new_price, new_qty, new_min_qty, image_url)


async def _post_with_slot(previous_slot: float, client: EldoradoClient, *args, **kwargs) -> tuple[str | None, int | None]:
    """Gọi _post_new_offer; nếu KHÔNG kịp gửi POST (lỗi ảnh -> ImageError, hoặc
    category lạ -> status None) thì trả lại lượt tạo cho dòng khác."""
    try:
        new_url, status = await _post_new_offer(client, *args, **kwargs)
    except (ImageError, MissingAttributesError):
        _release_create_slot(previous_slot)
        raise
    if status is None:
        _release_create_slot(previous_slot)
    return new_url, status


async def _upload_main_image(client: EldoradoClient, image_url: str) -> dict[str, str]:
    """Tải ảnh từ link (image_source) rồi upload lên Eldorado, trả về dict
    để gắn vào `details["mainOfferImage"]`. Mọi lỗi -> ImageError (người gọi
    PHẢI dừng trước khi xoá offer cũ)."""
    data, filename, content_type = await download_image(image_url)
    try:
        return await client.upload_offer_image(data, filename, content_type)
    except EldoradoApiError as e:
        raise ImageError(str(e)) from e
    except httpx.HTTPError as e:
        # timeout/mất kết nối khi upload — cũng là lỗi ảnh (offer cũ vẫn còn
        # nguyên), không để lọt ra thành "Lỗi không xác định" chung chung.
        raise ImageError(f"Upload ảnh lên Eldorado lỗi mạng: {type(e).__name__}") from e


async def _post_new_offer(
    client: EldoradoClient,
    offer: OwnOffer,
    new_price: Decimal,
    new_qty: int,
    new_min_qty: int,
    image_url: str | None = None,
) -> tuple[str | None, int | None]:
    """Build payload + gọi POST tạo offer mới — phần dùng chung giữa
    `create_new_offer` (429, có xoá offer cũ kèm theo) và
    `recreate_offer_from_snapshot` (404, không xoá gì cả)."""
    otype = offer.offer_type or offer.category
    if new_price * new_min_qty < 1:
        new_min_qty = round(1 / new_price) if new_price > 0 else 1
    if otype in ("Account", "CustomItem"):
        new_min_qty = 1

    details = copy.deepcopy(offer.raw_offer)
    for field in READ_ONLY_FIELDS:
        details.pop(field, None)
    details["pricing"] = {
        "quantity": new_qty,
        "minQuantity": new_min_qty,
        "pricePerUnit": {"amount": float(new_price), "currency": "USD"},
        "volumeDiscounts": offer.raw_offer.get("volumeDiscounts", []),
    }
    details["guaranteedDeliveryTime"] = offer.guaranteed_delivery_time
    details["description"] = offer.description
    details["deliveryMethod"] = offer.delivery_method

    # Ảnh CHỈ áp dụng cho Item (CustomItem) — offer mới không tự mang ảnh,
    # phải upload lại. Upload XONG mới POST tạo offer: nếu ảnh lỗi thì ImageError
    # nổ ra ở đây, TRƯỚC khi create_new_offer kịp xoá offer cũ (Item luôn tạo
    # mới rồi mới xoá cũ). Không có image_url -> giữ hành vi cũ (gửi lại nguyên
    # mainOfferImage của offer cũ trong raw_offer).
    if otype == "CustomItem" and image_url:
        details["mainOfferImage"] = await _upload_main_image(client, image_url)

    game_id = offer.raw_offer.get("gameId")
    trade_env_id = offer.trade_environment_values[-1]["id"] if offer.trade_environment_values else None
    offer_attributes = _build_offer_attributes(offer.raw_offer.get("attributes", []))
    if lacks_required_attributes(offer):
        logger.error("[create_new] Snapshot không có attribute (Orbs) -> KHÔNG gửi POST (chắc chắn 400)")
        raise MissingAttributesError(MISSING_ATTR_NOTE)

    if otype in ("Currency", "TopUp", "GiftCard"):
        url = "https://www.eldorado.gg/api/predefinedOffers/"
        payload = {
            "details": details,
            "augmentedGame": {
                "gameId": game_id, "category": offer.category,
                "tradeEnvironmentId": trade_env_id, "offerAttributes": offer_attributes,
                "attributeIdsCsv": None,
            },
            "category": offer.category,
        }
    elif otype == "Account":
        url = "https://www.eldorado.gg/api/flexibleOffers/account"
        payload = {
            "details": details,
            "augmentedGame": {
                "gameId": game_id, "category": "Account",
                "tradeEnvironmentId": trade_env_id, "offerAttributes": offer_attributes,
                "attributeIdsCsv": None,
            },
            "category": "Account",
        }
    elif otype == "CustomItem":
        # SỬA 2026-09-20 (bug thật bắt được khi test upload ảnh): URL cũ
        # `/me/offers` trả 404 — endpoint tạo Item thật là `/me/offers/item`
        # (cùng họ với endpoint update `/me/offers/item/{id}/details`) và đòi 2
        # trường `Details` + `AugmentedGame` (xác nhận qua lỗi 400 khi thăm dò
        # bằng body rỗng), giống shape payload update ở _build_details_payload.
        url = "https://www.eldorado.gg/api/v1/item-management/me/offers/item"
        payload = {
            "details": details,
            "augmentedGame": {
                "gameId": game_id, "category": offer.category,
                "tradeEnvironmentId": trade_env_id, "offerAttributes": offer_attributes,
                "attributeIdsCsv": None,
            },
        }
    else:
        logger.error("[create_new] Category không hỗ trợ tạo mới: %s", otype)
        return None, None

    resp = await client.post(url, payload)
    if resp.status_code not in (200, 201):
        logger.error("[create_new] Tạo offer mới thất bại: %s %s", resp.status_code, resp.text[:200])
        if resp.status_code == 429:
            # Chờ tăng dần thay vì nghỉ cố định: bị chặn ngắn thì ~1 phút sau
            # đã thử lại, bị chặn lâu thì giãn dần (không spam POST mỗi chu kỳ).
            until = offer_cache.record_create_429(
                time.time(), config.CREATE_BACKOFF_BASE_SECONDS, config.CREATE_BACKOFF_MAX_SECONDS,
                retry_after_seconds(resp), resp.text[:200],
            )
            logger.warning("[create_new] Tạo offer bị 429 -> thử tạo lại sau %s", _hhmm(until))
        return None, resp.status_code

    resp_json = resp.json()
    offer_obj = resp_json.get("offer")
    new_id = offer_obj.get("id") if isinstance(offer_obj, dict) else resp_json.get("id")
    if not new_id:
        # Đừng ghép ".../edit/None" thành link "thành công" — create_new_offer sẽ
        # xoá offer CŨ khi có link, sheet bị ghi đè link rác. Không đọc được id
        # thì coi như chưa chắc tạo được, KHÔNG xoá gì (có thể đã lỡ tạo 1 offer
        # mới — cần kiểm tra tay trên dashboard).
        logger.error("[create_new] Tạo offer trả %s nhưng không đọc được id mới: %s", resp.status_code, resp.text[:200])
        return None, resp.status_code
    new_url = f"https://www.eldorado.gg/dashboard/offers/{offer.category}/edit/{new_id}"
    offer_cache.record_create_success()

    return new_url, resp.status_code
