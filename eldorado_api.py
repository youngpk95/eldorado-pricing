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
import re
import time
from collections import deque
from decimal import Decimal
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

import config
import offer_cache
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


def resolve_attribute_value_id(value):
    """`value` của 1 attribute Eldorado trả về có thể là dict {"id": ...}
    HOẶC scalar (chuỗi/số) thẳng tuỳ game/category — DÙNG CHUNG ở đây
    (build_compare_url_from_sheet) và writer._build_offer_attributes, tránh
    2 bản sao dễ lệch nhau nếu Eldorado đổi shape lần nữa (đã từng đổi)."""
    return value.get("id") if isinstance(value, dict) else value


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
    thật của Eldorado, đã được xác nhận độc lập, không đơn giản hoá.

    QUAN TRỌNG (sửa 2026-09-17, bug thật đã gặp): tradeEnvironmentValueN +
    attribute (loại Orb/biến thể cụ thể) giờ LUÔN được tự chèn từ chính
    `trade_environment_values`/`attributes_raw` của OFFER CỦA MÌNH (đọc lúc
    fetch own offer), KHÔNG còn phụ thuộc việc link Compare URL dán vào sheet
    có chứa sẵn `te_v`/`attribute_value_id` hay không như trước. Trước đây
    nếu dán link kiểu `eldorado.gg/<game>/og/<uuid>?position=...` (Eldorado
    dùng khi chia sẻ thẳng 1 offer cụ thể) — link này KHÔNG mang theo
    `te_v`/`attribute_value_id` (thông tin lọc bị giấu trong uuid, chỉ trang
    web tự giải mã được) — filter bị bỏ trống hoàn toàn, kéo về NHẦM mọi biến
    thể khác trong cùng game/category (vd Mirror of Kalandra bị trộn lẫn với
    giá Chaos Orb rẻ mạt). Đã xác nhận trực tiếp qua API thật: thêm đúng 2
    filter này làm kết quả từ hàng chục dòng rác còn lại ĐÚNG 9 dòng, khớp số
    "Other sellers" trên trang web."""
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
    # Chỉ cho phép ĐOÁN attr_key từ 1 đoạn path của URL khi HOÀN TOÀN không
    # có attribute nào từ own offer (attributes_raw rỗng/None) — KHÔNG áp
    # dụng khi own offer CÓ trả về 1 attribute nhưng "id" của nó rỗng (response
    # méo/bất thường). Bug đã bị agent review phát hiện: trước đây id="" vẫn
    # lọt qua "in first_attr" rồi rơi vào `detail_attr_key or (path fallback)`
    # y hệt trường hợp thật sự không có gì — đoán bậy 1 đoạn path bất kỳ (vd
    # tên game) rồi gửi thẳng làm tên tham số lên API thật, thay vì an toàn
    # hơn là bỏ qua hẳn filter này.
    attr_key_guessable_from_path = not attributes_raw
    if attributes_raw:
        first_attr = attributes_raw[0]
        if isinstance(first_attr, dict) and "id" in first_attr:
            detail_attr_key = first_attr.get("id") or None
            detail_attr_value_id = resolve_attribute_value_id(first_attr.get("value"))

    renamed: dict[str, list[str]] = {}
    sheet_te_fallback: dict[int, str] = {}
    sheet_attr_value_fallback: str | None = None
    for key, values in params.items():
        # te_v/te_vN/attribute_value_id của URL SHEET không được lọt qua
        # NGUYÊN TRẠNG nữa — own offer luôn được ưu tiên (xem docstring ở
        # trên) — nhưng vẫn giữ lại làm FALLBACK (sheet_te_fallback/
        # sheet_attr_value_fallback) cho trường hợp own offer HOÀN TOÀN
        # không có dữ liệu tương ứng (own offer thiếu "value" ở đúng index đó
        # — đã xác nhận trade_environment_values có thể thiếu key "value" ở 1
        # số phần tử, xem guard "value" in item tương tự ở build_compare_url()
        # dòng 274 — hoặc attributes_raw rỗng/None hoàn toàn, bug đã bị agent
        # review phát hiện: trước đây rơi vào trường hợp này thì mất luôn
        # filter attribute, không còn cách nào cứu).
        if key == "te_v":
            sheet_te_fallback[0] = values[0] if values else ""
            continue
        elif key.startswith("te_v") and key[4:].isdigit():
            sheet_te_fallback[int(key[4:])] = values[0] if values else ""
            continue
        elif key == "attribute_value_id":
            sheet_attr_value_fallback = values[0] if values else None
            continue
        elif key == "attr_ids":
            renamed["offerAttributeIdsCsv"] = values
        else:
            renamed[key] = values

    for idx in set(te_value_map) | set(sheet_te_fallback):
        # own offer có mặt ở index này (kể cả "value" rỗng thật sự) LUÔN
        # thắng — chỉ rơi về sheet_te_fallback khi own offer HOÀN TOÀN không
        # có entry ở index đó. Dùng `in`/`te_value_map[idx]` thay vì
        # `te_value_map.get(idx) or ...` — bug thật vừa bị agent review phát
        # hiện: `or` coi own-offer value rỗng ("") giống hệt "không có",
        # khiến giá trị SHEET CŨ (có thể sai/lỗi thời) len vào thay vì đúng
        # ý own offer hiện tại (rỗng = không lọc theo chiều đó).
        value = te_value_map[idx] if idx in te_value_map else sheet_te_fallback.get(idx)
        if value:
            renamed[f"tradeEnvironmentValue{idx}"] = [value]

    # own offer LUÔN thắng nếu nó có giá trị; chỉ rơi về giá trị
    # attribute_value_id của sheet URL khi own offer HOÀN TOÀN không có
    # (detail_attr_value_id is None) — không dùng `or` vì lý do tương tự
    # tradeEnvironmentValue ở trên.
    resolved_attr_value = detail_attr_value_id if detail_attr_value_id is not None else sheet_attr_value_fallback
    if resolved_attr_value is not None:
        attr_key = detail_attr_key
        if attr_key is None and attr_key_guessable_from_path:
            path_parts = parsed.path.split("/")
            attr_key = path_parts[1] if len(path_parts) > 1 else ""
        if attr_key:
            renamed[attr_key] = [str(resolved_attr_value)]

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


IMAGE_UPLOAD_URL = API_BASE + "/files/me/Offer"
_UPLOADED_IMAGE_RE = re.compile(r"(Small|Large|Original)\.[A-Za-z0-9]+$")
_IMAGE_FIELD_BY_SIZE = {"Small": "smallImage", "Large": "largeImage", "Original": "originalSizeImage"}


def parse_uploaded_image_paths(local_paths) -> dict[str, str]:
    """Response upload ảnh của Eldorado trả `localPaths` = 3 đường dẫn
    `/offerimages/<tên>Small.png|Large|Original` (bắt từ trang sửa offer thật
    2026-09-20). Trong JSON offer, ảnh chính (`mainOfferImage`) lưu đúng 3 tên
    file TRẦN, KHÔNG có tiền tố `/offerimages/` — hàm này đổi sang dạng đó.
    Ném EldoradoApiError nếu thiếu bất kỳ cỡ nào."""
    result: dict[str, str] = {}
    for path in local_paths or []:
        name = str(path).rsplit("/", 1)[-1]
        m = _UPLOADED_IMAGE_RE.search(name)
        if m:
            result[_IMAGE_FIELD_BY_SIZE[m.group(1)]] = name
    missing = set(_IMAGE_FIELD_BY_SIZE.values()) - set(result)
    if missing:
        raise EldoradoApiError(f"Response upload ảnh thiếu {sorted(missing)} — Eldorado có thể đã đổi API")
    return result


class OfferNotFoundError(EldoradoApiError):
    """Offer không còn tồn tại (400/404) — tách riêng khỏi EldoradoApiError
    chung chung để product_pipeline.py xử lý khác đi: thử TỰ TẠO LẠI từ
    snapshot đã lưu (xem offer_cache.py) thay vì chỉ báo lỗi bắt sửa tay."""


class WriteStats:
    """Đếm lệnh GHI lên Eldorado (PUT/POST/DELETE) trong 60 phút gần nhất —
    để biết tài khoản bắt đầu bị 429 "Quota exceeded" ở mức bao nhiêu lệnh
    ghi (2026-11-14). Chỉ đếm trong tiến trình này: các máy/tool KHÁC dùng
    chung tài khoản cũng tiêu chung quota nhưng không nằm trong số này."""

    WINDOW_SECONDS = 3600

    def __init__(self) -> None:
        self._events: deque[tuple[float, str, int]] = deque()
        self._in_429 = False

    def _trim(self, now: float) -> None:
        while self._events and self._events[0][0] < now - self.WINDOW_SECONDS:
            self._events.popleft()

    def record(self, method: str, status: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._events.append((now, method, status))
        self._trim(now)
        if status == 429 and not self._in_429:
            self._in_429 = True
            logger.warning("[ghi] BẮT ĐẦU bị 429 — %s", self.summary(now))
        elif status != 429 and self._in_429:
            self._in_429 = False
            logger.info("[ghi] Hết bị 429 (lệnh %s trả %s) — %s", method, status, self.summary(now))

    def counts(self, now: float | None = None) -> dict[str, int]:
        now = time.time() if now is None else now
        self._trim(now)
        last10 = [e for e in self._events if e[0] >= now - 600]
        return {
            "60m": len(self._events),
            "60m_429": sum(1 for e in self._events if e[2] == 429),
            "10m": len(last10),
            "10m_429": sum(1 for e in last10 if e[2] == 429),
        }

    def summary(self, now: float | None = None) -> str:
        c = self.counts(now)
        return (
            f"lệnh ghi Eldorado: {c['60m']} trong 60 phút ({c['60m_429']} bị 429), "
            f"{c['10m']} trong 10 phút ({c['10m_429']} bị 429)"
        )


write_stats = WriteStats()


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """Header Retry-After (dạng số giây) nếu Eldorado có gửi; dạng ngày giờ
    hoặc giá trị lạ thì bỏ qua (dùng chờ tăng dần)."""
    headers = getattr(resp, "headers", None) or {}
    try:
        value = float(headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 24 * 3600 else None


def _is_light_write(method: str, url: str) -> bool:
    """Lệnh đổi giá riêng (changePrice / item .../price) hoặc POST (tạo offer,
    upload ảnh). 2026-11-14: có lúc CHỈ update đầy đủ (PUT .../details) bị 429
    còn changePrice vẫn chạy; còn khi "Quota exceeded" thì MỌI lệnh ghi đều
    429. Nên chỉ 429 ở các lệnh này mới coi là cả shop đang bị chặn ghi."""
    path = url.split("?", 1)[0].rstrip("/")
    return method == "POST" or path.endswith("/changePrice") or path.endswith("/price")


def _record_write_result(method: str, url: str, resp: httpx.Response) -> None:
    """changePrice/tạo bị 429 -> MỌI dòng tạm dừng ghi (offer_cache.record_write_429);
    ghi thành công -> bỏ tạm dừng. Xem writer.apply_update (kiểm tra trước khi ghi).
    PUT details bị 429 KHÔNG gây tạm dừng (changePrice có thể vẫn chạy)."""
    status = resp.status_code
    if status == 429 and _is_light_write(method, url):
        until = offer_cache.record_write_429(
            time.time(), config.WRITE_PAUSE_BASE_SECONDS, config.WRITE_PAUSE_MAX_SECONDS,
            retry_after_seconds(resp), (getattr(resp, "text", "") or "")[:200],
        )
        logger.warning("[ghi] 429 -> tạm dừng MỌI lệnh ghi tới %s", time.strftime("%H:%M:%S", time.localtime(until)))
    elif 200 <= status < 300:
        offer_cache.record_write_success()


class EldoradoClient:
    def __init__(self, auth) -> None:
        # auth: eldo_auth.EldoradoAuth (cookie) hoặc eldo_auth.SellerApiAuth
        # (Bearer token) — cùng giao diện get_auth_headers()/invalidate().
        self._auth = auth
        self._http = httpx.AsyncClient(timeout=config.ELDORADO_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self, content_type: bool = False) -> dict[str, str]:
        headers = {**BASE_HEADERS, **(await self._auth.get_auth_headers())}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    async def _request(self, method: str, url: str, content_type: bool = False, **kwargs) -> httpx.Response:
        """Gửi request kèm header đăng nhập; gặp 401 (token/cookie hết hạn
        sớm hoặc bị thu hồi) thì đăng nhập lại và thử đúng 1 lần nữa."""
        headers = await self._headers(content_type)
        resp = await self._http.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            logger.warning("[auth] Eldorado trả 401 (%s) — đăng nhập lại rồi thử lại 1 lần", method)
            # Truyền header ĐÃ dùng: nhiều dòng cùng 401 thì chỉ dòng đầu bắt
            # đăng nhập lại, các dòng sau dùng luôn token/cookie mới đó.
            self._auth.invalidate(headers)
            resp = await self._http.request(method, url, headers=await self._headers(content_type), **kwargs)
        return resp

    async def get_own_offer(self, offer_id: str, offer_type: str) -> tuple[OwnOffer, OfferUrls]:
        urls = get_api_urls(offer_id, offer_type)
        resp = await self._request("GET", urls.detail)
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
        resp = await self._request("GET", compare_url)
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
        resp = await self._request("PUT", url, content_type=True, json=payload)
        write_stats.record("PUT", resp.status_code)
        _record_write_result("PUT", url, resp)
        return resp

    async def post(self, url: str, payload: dict) -> httpx.Response:
        await self._pace_write()
        resp = await self._request("POST", url, content_type=True, json=payload)
        write_stats.record("POST", resp.status_code)
        _record_write_result("POST", url, resp)
        return resp

    async def upload_offer_image(self, data: bytes, filename: str, content_type: str) -> dict[str, str]:
        """Upload 1 ảnh offer (multipart, field `image`) — trả về dict
        smallImage/largeImage/originalSizeImage để gắn vào `mainOfferImage`.
        KHÔNG ép Content-Type: json (httpx tự đặt multipart + boundary)."""
        await self._pace_write()
        resp = await self._request("POST", IMAGE_UPLOAD_URL, files={"image": (filename, data, content_type)})
        write_stats.record("UPLOAD", resp.status_code)
        _record_write_result("POST", IMAGE_UPLOAD_URL, resp)
        if resp.status_code != 200:
            raise EldoradoApiError(f"Upload ảnh lên Eldorado thất bại (status {resp.status_code}) — {resp.text[:200]}")
        try:
            local_paths = resp.json().get("localPaths")
        except ValueError as e:
            raise EldoradoApiError("Response upload ảnh không phải JSON") from e
        return parse_uploaded_image_paths(local_paths)

    async def delete(self, url: str) -> httpx.Response:
        await self._pace_write()
        # DELETE KHÔNG tính vào tạm dừng ghi: khi "Quota exceeded" DELETE vẫn
        # chạy (test 2026-11-14), 204 ở đây không có nghĩa là hết bị chặn ghi.
        resp = await self._request("DELETE", url, content_type=True)
        write_stats.record("DELETE", resp.status_code)
        return resp


def filter_competitors(
    raw_results: list[dict],
    blacklist: set[str],
    min_stock: float | None,
    min_feedback: float | None,
    tile_feedback: float | None,
    floor_price: Decimal | None,
    exclude_keywords: set[str] = frozenset(),
    require_keywords: set[str] = frozenset(),
) -> tuple[list[CompetitorMatch], list[CompetitorMatch]]:
    """Trả về (đối thủ đạt điều kiện để tính giá, đối thủ giá THẤP HƠN giá
    sàn — chỉ để cảnh báo, không dùng để tính giá) — port từ vòng lặp trong
    Eldorado.list_eldo. Đã bỏ lọc theo thời gian giao hàng (không dùng tới,
    theo yêu cầu user 2026-09-13 — xem eldorado_repricer_project.md nếu cần
    thêm lại). Lọc theo từ khoá tiêu đề (exclude_keywords/require_keywords)
    đã được thêm lại theo yêu cầu 2026-09-15."""
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
            title = (offer.get("offerTitle") or "").lower()
            has_excluded = any(k in title for k in exclude_keywords)
            has_required = not require_keywords or any(k in title for k in require_keywords)

            is_match = (
                (not min_stock or qty >= min_stock)
                and (not min_feedback or rating >= min_feedback)
                and (not tile_feedback or feedback >= tile_feedback)
                and (seller not in blacklist)
                and (floor_price is None or price >= floor_price)
                and not has_excluded
                and has_required
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
