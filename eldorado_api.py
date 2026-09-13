"""Gọi API thật của Eldorado — build URL theo 4 loại offer, lấy chi tiết offer
của mình, build URL so sánh đối thủ từ link sheet, lấy + lọc danh sách đối
thủ. Port gần như nguyên vẹn logic từ eldo.py (bản gốc) — đây là phần chứa
nhiều quirk thật của API Eldorado (đã xác nhận qua agent review độc lập),
KHÔNG đơn giản hoá, chỉ đổi sang async/httpx + kiểu dữ liệu rõ ràng hơn.

KHÔNG còn phần G2G/FunPay (đã bỏ theo yêu cầu) — file này CHỈ so đối thủ khác
trên chính Eldorado."""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

import config
from models import CompareResult, CompetitorMatch, OfferUrls, OwnOffer

logger = logging.getLogger(__name__)

API_BASE = "https://www.eldorado.gg/api"

URLS = {
    "predefined": {
        "compare": API_BASE + "/predefinedOffers/augmentedGame/{id}/?pageIndex=1&pageSize=20",
        "detail": API_BASE + "/predefinedOffers/{id}",
        "update": API_BASE + "/predefinedOffers/{id}/details",
        "change": API_BASE + "/predefinedOffersUser/me/{id}/changePrice",
    },
    "custom_item": {
        "detail": API_BASE + "/v1/item-management/offers/{id}",
        "update": API_BASE + "/v1/item-management/me/offers/item/{id}/details",
        "price": API_BASE + "/v1/item-management/me/offers/{id}/price",
    },
    "flexible": {
        "detail": API_BASE + "/flexibleOffers/{id}",
        "update": API_BASE + "/flexibleOffers/{type}/{id}/details",
        "change": API_BASE + "/flexibleOffersUser/me/{id}/changePrice",
    },
}

BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "User-Agent": "CNLTeam-Bot-662flSNueD",
}

_OFFER_TYPE_ALIASES = {
    "customitem": "CustomItem",
    "topup": "TopUp",
    "currency": "Currency",
    "giftcard": "GiftCard",
    "account": "Account",
}


def _normalize_offer_type(offer_type: str) -> str:
    return _OFFER_TYPE_ALIASES.get(str(offer_type).strip().lower(), str(offer_type).strip())


def parse_offer_link(product_url: str) -> tuple[str, str]:
    """Tách offerID + offerType từ link sản phẩm (link dashboard hoặc query
    param) — port từ Eldorado._parse_offer_link."""
    offer_id, offer_type = "", ""
    try:
        parsed = urlparse(product_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        queries = parse_qs(parsed.query)

        if "dashboard" in path_parts and "offers" in path_parts:
            idx = path_parts.index("offers")
            if len(path_parts) > idx + 1 and path_parts[idx + 1] != "edit":
                offer_type = path_parts[idx + 1]
            offer_id = path_parts[-1] if path_parts else ""

        if not offer_type or offer_type.lower() == "edit":
            offer_type = (queries.get("type") or queries.get("category") or queries.get("offerType") or [""])[0]

        if not offer_id and path_parts:
            offer_id = path_parts[-1]
    except Exception as e:
        logger.error("[parse] Không thể tách ID từ link %s: %s", product_url, e)
    return offer_id, offer_type


def get_api_urls(offer_id: str, offer_type: str) -> OfferUrls:
    otype = _normalize_offer_type(offer_type)
    if otype in ("Currency", "TopUp", "GiftCard"):
        tmpl = URLS["predefined"]
        return OfferUrls(
            detail=tmpl["detail"].format(id=offer_id),
            compare="",  # build riêng từ sheet link, xem build_compare_url()
            update=tmpl["update"].format(id=offer_id),
            change=tmpl["change"].format(id=offer_id),
        )
    if otype == "CustomItem":
        tmpl = URLS["custom_item"]
        return OfferUrls(
            detail=tmpl["detail"].format(id=offer_id),
            compare="",
            update=tmpl["update"].format(id=offer_id),
            change=tmpl["price"].format(id=offer_id),
        )
    if otype == "Account":
        tmpl = URLS["flexible"]
        return OfferUrls(
            detail=tmpl["detail"].format(id=offer_id),
            compare="",
            update=tmpl["update"].format(type="account", id=offer_id),
            change=tmpl["change"].format(id=offer_id),
        )
    raise ValueError(f"offer_type không nhận diện được: {offer_type!r}")


def build_compare_url_from_sheet(
    sheet_url: str,
    game_id: str,
    category: str,
    trade_environment_values: list[dict] | None,
    attributes_raw: list[dict] | None,
) -> str:
    """Port gần như nguyên vẹn từ Eldorado._build_compare_url_from_sheet —
    remap tham số kiểu storefront (te_v/te_vN/attr_ids/attribute_value_id)
    sang tham số API thật (tradeEnvironmentValueN/offerAttributeIdsCsv/...).
    Xem docstring gốc trong eldo.py cho ví dụ cụ thể — logic này encode quirk
    thật của Eldorado, đã được xác nhận độc lập, không đơn giản hoá."""
    parsed = urlparse(sheet_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    page_index = params.pop("gamePageOfferIndex", ["1"])[0]
    page_size = params.pop("gamePageOfferSize", ["24"])[0]

    te_value_map: dict[int, str] = {}
    for idx, item in enumerate(trade_environment_values or []):
        if "value" in item:
            te_value_map[idx] = item["value"]

    detail_attr_key = None
    detail_attr_value_id = None
    if attributes_raw:
        first_attr = attributes_raw[0]
        if isinstance(first_attr, dict):
            detail_attr_key = first_attr.get("id")
            attr_val = first_attr.get("value")
            if isinstance(attr_val, dict) and "id" in attr_val:
                detail_attr_value_id = attr_val["id"]

    has_te_v = any(k == "te_v" or (k.startswith("te_v") and k[4:].isdigit()) for k in params)

    renamed: dict[str, list[str]] = {}
    for key, values in params.items():
        if key == "te_v":
            renamed["tradeEnvironmentValue"] = [te_value_map.get(0, values[0] if values else "")]
        elif key.startswith("te_v") and key[4:].isdigit():
            idx = int(key[4:])
            renamed[f"tradeEnvironmentValue{idx}"] = [te_value_map.get(idx, values[0] if values else "")]
        elif key == "attribute_value_id" and has_te_v:
            attr_key = detail_attr_key or (parsed.path.split("/")[1] if len(parsed.path.split("/")) > 1 else "")
            attr_val = detail_attr_value_id or (values[0] if values else "")
            if attr_key:
                renamed[attr_key] = [attr_val]
        elif key == "attr_ids":
            renamed["offerAttributeIdsCsv"] = values
        else:
            renamed[key] = values
    params = renamed

    extra_params = urlencode(params, doseq=True, quote_via=quote)
    cat = str(category).strip()

    if cat == "Account":
        url = f"{API_BASE}/flexibleOffers/?gameId={game_id}&category={category}&pageIndex={page_index}&pageSize={page_size}"
    elif cat in ("Currency", "TopUp", "GiftCard"):
        url = f"{API_BASE}/predefinedOffers/augmentedGame/offers?gameId={game_id}&category={category}&pageIndex={page_index}&pageSize={page_size}"
    else:
        url = f"{API_BASE}/v1/item-management/offers?gameId={game_id}&category={category}&pageIndex={page_index}&pageSize={page_size}&includeDeliveryMedians=true"
    if extra_params:
        url += f"&{extra_params}"
    return url


class EldoradoApiError(RuntimeError):
    pass


class OfferNotFoundError(EldoradoApiError):
    """Offer không còn tồn tại (400/404) — tách riêng khỏi EldoradoApiError
    chung chung để product_pipeline.py xử lý khác đi: thử TỰ TẠO LẠI từ
    snapshot đã lưu (xem offer_cache.py) thay vì chỉ báo lỗi bắt sửa tay."""


class EldoradoClient:
    def __init__(self, get_cookie) -> None:
        self._get_cookie = get_cookie  # async callable () -> str
        self._http = httpx.AsyncClient(timeout=config.ELDORADO_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self, content_type: bool = False) -> dict[str, str]:
        cookie = await self._get_cookie()
        headers = {**BASE_HEADERS, "Cookie": cookie}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    async def get_own_offer(self, offer_id: str, offer_type: str) -> tuple[OwnOffer, OfferUrls]:
        urls = get_api_urls(offer_id, offer_type)
        headers = await self._headers()
        resp = await self._http.get(urls.detail, headers=headers)
        if resp.status_code in (400, 404):
            raise OfferNotFoundError("Không tìm thấy ID sản phẩm")
        if resp.status_code != 200:
            raise EldoradoApiError(f"Lỗi lấy chi tiết sản phẩm (status {resp.status_code})")

        data = resp.json()["offer"]
        trade_env = data.get("tradeEnvironmentValues", [])
        offer = OwnOffer(
            offer_id=offer_id,
            offer_type=_normalize_offer_type(offer_type),
            category=data["category"],
            game_id=str(data["gameId"]),
            price=Decimal(str(data["pricePerUnit"]["amount"])),
            quantity=int(data["quantity"]),
            min_quantity=int(data.get("minQuantity", 1)),
            description=data.get("description", ""),
            offer_title=data.get("offerTitle", ""),
            guaranteed_delivery_time=data.get("guaranteedDeliveryTime", ""),
            delivery_method=data.get("deliveryMethod", ""),
            trade_environment_values=trade_env,
            attributes_raw=data.get("attributes", []),
            raw_offer=data,
        )
        return offer, urls

    def build_compare_url(self, offer: OwnOffer, product_compare_url: str) -> str:
        if product_compare_url:
            return build_compare_url_from_sheet(
                product_compare_url,
                offer.game_id,
                offer.category,
                offer.trade_environment_values,
                offer.attributes_raw,
            )
        # Fallback: build từ chính detail response (bản gốc chỉ làm điều
        # này cho Currency/TopUp/GiftCard) — port nguyên logic đó.
        query_parts = ["pageIndex=1", "pageSize=24"]
        for idx, item in enumerate(offer.trade_environment_values):
            if "value" in item:
                query_parts.append(f"tradeEnvironmentValue{idx}={item['value']}")
        return f"{API_BASE}/predefinedOffers/augmentedGame/offers?gameId={offer.game_id}&category={offer.category}&{'&'.join(query_parts)}"

    async def get_competitors(self, compare_url: str) -> list[dict]:
        headers = await self._headers()
        resp = await self._http.get(compare_url, headers=headers)
        if resp.status_code == 429:
            raise EldoradoApiError("Eldorado giới hạn tần suất yêu cầu (429)")
        if resp.status_code != 200:
            raise EldoradoApiError(f"Lỗi truy vấn so sánh đối thủ (status {resp.status_code})")
        data = resp.json()
        if isinstance(data, dict):
            return data.get("results", [])
        return data if isinstance(data, list) else []

    @staticmethod
    async def _pace_write() -> None:
        """Giãn nhẹ trước mỗi request GHI lên Eldorado — né 429 chủ động
        thay vì chỉ xử lý phản ứng (xoá+tạo lại) như bản gốc."""
        if config.ELDORADO_WRITE_DELAY_MS > 0:
            await asyncio.sleep(config.ELDORADO_WRITE_DELAY_MS / 1000)

    async def put(self, url: str, payload: dict) -> httpx.Response:
        await self._pace_write()
        headers = await self._headers(content_type=True)
        return await self._http.put(url, headers=headers, json=payload)

    async def post(self, url: str, payload: dict) -> httpx.Response:
        await self._pace_write()
        headers = await self._headers(content_type=True)
        return await self._http.post(url, headers=headers, json=payload)

    async def delete(self, url: str) -> httpx.Response:
        await self._pace_write()
        headers = await self._headers(content_type=True)
        return await self._http.delete(url, headers=headers)


def filter_competitors(
    raw_results: list[dict],
    blacklist: set[str],
    min_stock: float | None,
    min_feedback: float | None,
    tile_feedback: float | None,
    floor_price: Decimal | None,
) -> tuple[list[CompetitorMatch], list[CompetitorMatch]]:
    """Trả về (đối thủ đạt điều kiện để tính giá, đối thủ giá THẤP HƠN giá
    sàn — chỉ để cảnh báo, không dùng để tính giá) — port từ vòng lặp trong
    Eldorado.list_eldo. Đã bỏ lọc theo thời gian giao hàng + từ khoá tiêu đề
    (không dùng tới, theo yêu cầu user 2026-09-13 — xem
    eldorado_repricer_project.md nếu cần thêm lại)."""
    qualifying: list[CompetitorMatch] = []
    below_floor: list[CompetitorMatch] = []

    for item in raw_results:
        if not item:
            continue
        try:
            seller = (item.get("user") or {}).get("username", "Unknown")
            offer = item.get("offer") or {}
            price = Decimal(str((offer.get("pricePerUnit") or {}).get("amount", 0)))
            qty = offer.get("quantity", 0)
            user_order_info = item.get("userOrderInfo") or {}
            rating = float(user_order_info.get("ratingCount", 0))
            feedback = round(float(user_order_info.get("feedbackScore", 0)), 0)

            is_match = (
                (not min_stock or qty >= min_stock)
                and (not min_feedback or rating >= min_feedback)
                and (not tile_feedback or feedback >= tile_feedback)
                and (seller not in blacklist)
                and (floor_price is None or price >= floor_price)
            )
            if is_match:
                qualifying.append(CompetitorMatch(seller=seller, price=price))
            elif floor_price is not None and price < floor_price:
                below_floor.append(CompetitorMatch(seller=seller, price=price))
        except Exception as e:
            logger.warning("[compare] Bỏ qua 1 dòng đối thủ lỗi: %s", e)

    return qualifying, below_floor


def pick_cheapest(matches: list[CompetitorMatch]) -> CompetitorMatch | None:
    if not matches:
        return None
    return min(matches, key=lambda m: m.price)
