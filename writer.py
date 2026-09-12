"""Ghi thay đổi lên Eldorado thật — port từ req_Eldo.py (put_eldo/create_new),
dùng EldoradoClient (httpx.AsyncClient) thay requests đồng bộ.

Đơn giản hoá có chủ đích so với bản gốc: bản gốc có 2 nhánh gần như trùng
lặp trong checkupdate.py (thử URL_UPDATE trước rồi mới URL_CHANGE, HOẶC
ngược lại, tuỳ field nào đổi) — gộp thành 1 hàm duy nhất `apply_update()`:
nếu giá đổi thì thử endpoint đổi giá (nhẹ, nhanh) trước, fallback sang
endpoint update đầy đủ; nếu giá KHÔNG đổi (chỉ stock/thời gian/minQty) thì chỉ
cần endpoint update đầy đủ.

Khi gặp 429 (quá tải), fallback sang xoá + tạo lại offer VỚI GIÁ MỚI luôn
(không phải tạo lại giá cũ rồi đổi giá sau). Theo xác nhận trực tiếp từ user
(2026-09-13): tạo offer MỚI không bao giờ bị 429 (chỉ endpoint đổi
giá/update mới bị) — nên chỉ cần đúng 1 lần tạo là xong, KHÔNG cần retry
loop. Nếu chu kỳ SAU vẫn gặp 429 khi đổi giá (vì offer vừa tạo lại cũng cũ
đi theo thời gian), `product_pipeline.py` sẽ tự lặp lại đúng luồng này —
xoá + tạo lại lần nữa với giá mới nhất tại thời điểm đó. Vì offer mới có ID
khác hẳn, sheet phải tự cập nhật lại `My Listing URL` (cột F) sang link mới
— xem `product_pipeline.py`, nếu không chu kỳ sau sẽ tìm nhầm offer đã bị
xoá."""
from __future__ import annotations

import copy
import logging
from decimal import Decimal

import httpx

import config
from eldorado_api import EldoradoClient
from models import OfferUrls, OwnOffer

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
        value = attr.get("value")
        value_id = value.get("id") if isinstance(value, dict) else value
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
    allow_create_new: bool,
    create_new_enabled: bool,
) -> tuple[bool, str, str | None]:
    """Trả về (thành công, mô tả, link offer mới nếu vừa tạo lại)."""
    details_payload = _build_details_payload(offer, new_price, new_qty, new_min_qty)
    price_only_payload = {"amount": float(new_price), "currency": "USD"}

    responses: list[httpx.Response] = []

    if price_changed:
        resp_price = await client.put(urls.change, price_only_payload)
        responses.append(resp_price)
        if resp_price.status_code == 200:
            return True, "Đổi giá thành công (change price)", None
        resp_full = await client.put(urls.update, details_payload)
        responses.append(resp_full)
        if resp_full.status_code == 200:
            return True, "Cập nhật thành công (fallback: update đầy đủ)", None
    else:
        resp_full = await client.put(urls.update, details_payload)
        responses.append(resp_full)
        if resp_full.status_code == 200:
            return True, "Cập nhật thành công (stock/thời gian/minQty)", None

    is_429 = any(r.status_code == 429 for r in responses)
    status_summary = ", ".join(f"{r.status_code}" for r in responses)
    if not (is_429 and allow_create_new and create_new_enabled):
        body_texts = "; ".join(r.text[:200] for r in responses)
        return False, f"Cập nhật thất bại (status: {status_summary}) — {body_texts}", None

    # 429 -> xoá + tạo lại VỚI GIÁ MỚI. Tạo offer mới không bị 429 (xác nhận
    # từ user) nên chỉ cần đúng 1 lần thử — không cần retry loop.
    new_link, create_status = await create_new_offer(client, offer, new_price, new_qty, new_min_qty)
    if new_link:
        return True, "429 rate-limit -> xoá + tạo offer mới thành công", new_link
    return False, f"429 rate-limit -> tạo offer mới cũng thất bại (status {create_status})", None


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
) -> tuple[str | None, int | None]:
    """Xoá offer cũ + tạo offer mới hoàn toàn VỚI GIÁ MỚI luôn — Eldorado's
    429 rate-limit workaround, port từ RequestEldo.create_new. Thứ tự xoá
    trước/sau tuỳ category, giữ đúng như bản gốc. Trả về (link offer mới
    hoặc None, status code của chính request tạo — None nếu lỗi xảy ra
    trước khi kịp gọi request)."""
    otype = offer.offer_type or offer.category
    url_del = _delete_url_for_category(otype, offer.offer_id)
    should_delete_before = otype in ("Currency", "TopUp", "GiftCard")

    if should_delete_before:
        try:
            resp = await client.delete(url_del)
            if resp.status_code not in (200, 204):
                logger.warning("[create_new] Xoá offer cũ trước khi tạo mới thất bại: %s", resp.status_code)
        except Exception as e:
            logger.error("[create_new] Lỗi xoá offer cũ trước: %s", e)

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

    game_id = offer.raw_offer.get("gameId")
    trade_env_id = offer.trade_environment_values[-1]["id"] if offer.trade_environment_values else None
    offer_attributes = _build_offer_attributes(offer.raw_offer.get("attributes", []))

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
        url = "https://www.eldorado.gg/api/v1/item-management/me/offers"
        payload = {"details": details, "gameId": game_id, "category": "CustomItem", "tradeEnvironmentId": trade_env_id}
    else:
        logger.error("[create_new] Category không hỗ trợ tạo mới: %s", otype)
        return None, None

    resp = await client.post(url, payload)
    if resp.status_code not in (200, 201):
        logger.error("[create_new] Tạo offer mới thất bại: %s %s", resp.status_code, resp.text[:200])
        return None, resp.status_code

    resp_json = resp.json()
    offer_obj = resp_json.get("offer")
    new_id = offer_obj.get("id") if isinstance(offer_obj, dict) else resp_json.get("id")
    new_url = f"https://www.eldorado.gg/dashboard/offers/{offer.category}/edit/{new_id}"

    if not should_delete_before:
        try:
            resp_del = await client.delete(url_del)
            if resp_del.status_code not in (200, 204):
                logger.warning("[create_new] Xoá offer cũ SAU khi tạo mới thất bại: %s", resp_del.status_code)
        except Exception as e:
            logger.error("[create_new] Lỗi xoá offer cũ sau: %s", e)

    return new_url, resp.status_code
