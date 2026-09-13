"""Xử lý 1 sản phẩm/1 chu kỳ — port từ checkupdate.py + phần tương ứng trong
main.py process_product (bản gốc), đã bỏ G2G/FunPay VÀ bỏ hẳn cơ chế tham
chiếu chéo sheet khác (mọi giá trị nằm thẳng trong dòng — schema phẳng giống
tool G2G Repricer sibling, theo yêu cầu user 2026-09-13)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import config
import eldorado_api as eldo
import offer_cache
import pricing
import writer
from eldorado_api import EldoradoClient, EldoradoApiError, OfferNotFoundError
from models import OwnOffer, ProductRow
from sheets_client import SheetsClient

logger = logging.getLogger(__name__)


def _to_decimal(value: str, default: Decimal | None = None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return default


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _is_checked(value: str) -> bool:
    """Dùng chung cho mọi cột checkbox thật (Data Validation BOOLEAN) —
    Sheets API trả về chuỗi "TRUE"/"FALSE", không phải "1"/"0"; vẫn chấp
    nhận cả "1" để không phụ thuộc 1 định dạng duy nhất (vd nếu ai đó gõ tay
    thay vì tích checkbox)."""
    return (value or "").strip().upper() in ("TRUE", "1")


@dataclass
class RowConfig:
    """Đọc + gõ kiểu mọi cột cần dùng của 1 dòng sheet — schema PHẲNG, mọi
    giá trị nằm thẳng trong dòng này, không còn tham chiếu chéo sheet khác."""

    row: ProductRow

    @property
    def is_checked_enabled(self) -> bool:
        return _is_checked(self.row.get("ENABLED"))

    @property
    def enabled(self) -> bool:
        return self.is_checked_enabled and bool(self.row.get("OWN_LISTING_URL")) and bool(self.row.get("COMPARE_URL"))

    @property
    def name(self) -> str:
        return self.row.get("NAME") or f"row{self.row.index + 1}"

    @property
    def own_listing_url(self) -> str:
        return self.row.get("OWN_LISTING_URL")

    @property
    def compare_url(self) -> str:
        return self.row.get("COMPARE_URL")

    @property
    def stock(self) -> int:
        return _to_int(self.row.get("STOCK"), 0)

    @property
    def price_min(self) -> Decimal | None:
        return _to_decimal(self.row.get("PRICE_MIN"))

    @property
    def price_max(self) -> Decimal | None:
        return _to_decimal(self.row.get("PRICE_MAX"))

    @property
    def discount_amount(self) -> Decimal:
        return _to_decimal(self.row.get("DISCOUNT_AMOUNT"), Decimal(0))

    @property
    def round_decimals(self) -> int:
        return _to_int(self.row.get("ROUND_DECIMALS"), 6)

    @property
    def always_undercut(self) -> bool:
        return _is_checked(self.row.get("ALWAYS_UNDERCUT"))

    @property
    def min_purchase_base(self) -> Decimal | None:
        return _to_decimal(self.row.get("MIN_PURCHASE_BASE"))

    @property
    def min_purchase_coef(self) -> Decimal:
        return _to_decimal(self.row.get("MIN_PURCHASE_COEF"), Decimal(1))

    @property
    def competitor_stock_min(self) -> float | None:
        v = self.row.get("COMPETITOR_STOCK_MIN")
        return float(v) if v else None

    @property
    def competitor_min_rating_count(self) -> float | None:
        v = self.row.get("COMPETITOR_MIN_RATING_COUNT")
        return float(v) if v else None

    @property
    def competitor_min_feedback_percent(self) -> float | None:
        v = self.row.get("COMPETITOR_MIN_FEEDBACK_PERCENT")
        return float(v) if v else None

    @property
    def seller_blacklist(self) -> set[str]:
        names = {n.strip() for n in self.row.get("SELLER_BLACKLIST").split(";") if n.strip()}
        return names | {"CNLTeam"}  # luôn tự loại chính mình khỏi danh sách đối thủ

    @property
    def create_new_enabled(self) -> bool:
        return _is_checked(self.row.get("ALLOW_RECREATE_ON_RATE_LIMIT"))

    @property
    def relax_seconds(self) -> int | None:
        """Cấu hình cho CẢ VÒNG CHẠY (nghỉ sau khi xong hết sản phẩm), không
        phải riêng dòng này — xem main.py nơi tổng hợp giá trị lớn nhất giữa
        mọi sản phẩm đang bật. None nếu để trống (dùng mặc định .env)."""
        v = self.row.get("RELAX_SECONDS")
        return _to_int(v, None) if v else None


async def _write_result(sheets: SheetsClient, index: int, note: str, link: str | None) -> None:
    """SheetsClient dùng googleapiclient đồng bộ — chạy trong thread pool mặc
    định của asyncio để không chặn event loop trong lúc chờ HTTP."""
    await asyncio.to_thread(sheets.write_result, index, note, link)


async def _save_snapshot(row_index: int, own_listing_url: str, offer: OwnOffer) -> None:
    """offer_cache dùng I/O file đồng bộ — chạy trong thread pool, cùng lý do
    và cùng quy ước với _write_result ở trên (không chặn event loop lúc nhiều
    dòng đang xử lý song song, xem main.py asyncio.gather)."""
    await asyncio.to_thread(offer_cache.save_snapshot, row_index, own_listing_url, offer)


async def _load_snapshot(row_index: int, expected_own_listing_url: str) -> tuple[OwnOffer, str | None] | None:
    return await asyncio.to_thread(offer_cache.load_snapshot, row_index, expected_own_listing_url)


async def _mark_pending_link(row_index: int, link: str) -> None:
    await asyncio.to_thread(offer_cache.mark_pending_link, row_index, link)


async def _compute_target(
    cfg: RowConfig, offer: OwnOffer, client: EldoradoClient
) -> tuple[Decimal, int, int, list[str]]:
    """Tính giá/stock/minQty MỚI + note mô tả đối thủ — dùng chung cho cả
    luồng bình thường (offer còn sống) lẫn luồng phục hồi sau 404
    (_recover_missing_offer, tính trên offer LẤY TỪ SNAPSHOT thay vì fetch
    trực tiếp, nhưng công thức tính giá phải giống hệt nhau, không được lệch)."""
    compare_url = client.build_compare_url(offer, cfg.compare_url)
    raw_competitors = await client.get_competitors(compare_url)
    qualifying, below_floor = eldo.filter_competitors(
        raw_competitors,
        blacklist=cfg.seller_blacklist,
        min_stock=cfg.competitor_stock_min,
        min_feedback=cfg.competitor_min_rating_count,
        tile_feedback=cfg.competitor_min_feedback_percent,
        floor_price=cfg.price_min,
    )
    cheapest = eldo.pick_cheapest(qualifying)

    new_price = pricing.calculate_new_price(
        current_price=offer.price,
        min_competitor_price=cheapest.price if cheapest else None,
        discount_amount=cfg.discount_amount,
        price_min=cfg.price_min,
        price_max=cfg.price_max,
        undercut_from_competitor=cfg.always_undercut,
        round_decimals=cfg.round_decimals,
    )

    new_stock = cfg.stock
    new_min_qty = offer.min_quantity
    if cfg.min_purchase_base is not None and new_price > 0:
        new_min_qty = pricing.find_min_quantity(cfg.min_purchase_base, new_price, cfg.min_purchase_coef)

    note_lines = [
        f"Giá: {offer.price} -> {new_price} | Stock: {offer.quantity} -> {new_stock} | "
        f"minQty: {offer.min_quantity} -> {new_min_qty}"
    ]
    if cheapest:
        note_lines.append(f"Đối thủ rẻ nhất hợp lệ: {cheapest.seller} = {cheapest.price}")
    else:
        note_lines.append("Không có đối thủ Eldorado nào đạt điều kiện để tính giá.")
    if below_floor:
        preview = ", ".join(f"{m.seller}={m.price}" for m in below_floor[:5])
        note_lines.append(f"⚠️ {len(below_floor)} đối thủ giá THẤP HƠN giá sàn (bị loại khi tính giá): {preview}")

    return new_price, new_stock, new_min_qty, note_lines


async def _recover_missing_offer(
    row: ProductRow, cfg: RowConfig, sheets: SheetsClient, client: EldoradoClient
) -> None:
    """Offer đã 404 (chắc chắn bị xoá, không phải lỗi tạm thời) — thử TỰ TẠO
    LẠI từ snapshot đã lưu (offer_cache.py) thay vì chỉ báo lỗi bắt sửa tay.

    Bug thật đã gặp (2026-09-13, dòng "Mirror COTA"): 429 -> xoá + tạo lại
    offer mới thành công, nhưng ghi link mới về sheet thất bại -> mọi chu kỳ
    sau đọc lại link CŨ đã xoá -> 404 mãi mãi, không có cách nào tự hồi phục,
    phải sửa tay. Hàm này đóng vòng lặp đó lại.

    Dùng chung checkbox ALLOW_RECREATE_ON_RATE_LIMIT với luồng 429 (cùng ý
    nghĩa: "cho phép tool tự xoá/tạo lại offer khi cần") — không thêm cột
    riêng để tránh phải chạy lại setup_sheet_headers.py.

    Chặn spam tạo trùng: nếu snapshot đã có `pending_recreated_link` (đã
    từng tự tạo lại cho lần 404 này rồi, chỉ là chưa chắc ghi được vào sheet)
    thì KHÔNG tạo thêm offer mới — chỉ thử ghi lại đúng link đó vào sheet."""
    if not cfg.create_new_enabled:
        await _write_result(
            sheets, row.index,
            "Lỗi: không tìm thấy ID sản phẩm (offer đã bị xoá) — bật 'Allow Recreate on Rate Limit' "
            "để tool tự tạo lại offer mới, hoặc tự tạo lại tay rồi dán link vào My Listing URL.",
            None,
        )
        return

    snapshot = await _load_snapshot(row.index, cfg.own_listing_url)
    if snapshot is None:
        await _write_result(
            sheets, row.index,
            "Lỗi: không tìm thấy ID sản phẩm (offer đã bị xoá) và không có dữ liệu backup để tự tạo lại "
            "— cần tự tạo lại tay rồi dán link mới vào My Listing URL.",
            None,
        )
        return

    snapshot_offer, pending_link = snapshot

    if pending_link:
        await _write_result(
            sheets, row.index,
            f"⚠️ Offer đã bị xoá — đã tự tạo lại offer mới trước đó nhưng chưa ghi được vào sheet, đang thử ghi lại: {pending_link}",
            pending_link,
        )
        return

    new_price, new_stock, new_min_qty, note_lines = await _compute_target(cfg, snapshot_offer, client)

    if config.DRY_RUN:
        note_lines.insert(0, "[DRY RUN] Offer đã bị xoá — sẽ tự tạo lại offer mới (chưa làm thật).")
        await _write_result(sheets, row.index, "\n".join(note_lines), None)
        return

    new_link, create_status = await writer.recreate_offer_from_snapshot(
        client, snapshot_offer, new_price, new_stock, new_min_qty
    )
    if not new_link:
        note_lines.insert(0, f"❌ Offer đã bị xoá, tự tạo lại THẤT BẠI (status {create_status}) — cần tự tạo lại tay.")
        await _write_result(sheets, row.index, "\n".join(note_lines), None)
        return

    # Ghi pending TRƯỚC khi thử ghi sheet — nếu tiến trình crash ngay sau
    # đây, link vẫn còn trong cache để lần chạy sau không tạo trùng thêm.
    await _mark_pending_link(row.index, new_link)
    note_lines.insert(0, "✅ Offer đã bị xoá -> đã TỰ TẠO LẠI offer mới thành công, đã cập nhật My Listing URL.")
    await _write_result(sheets, row.index, "\n".join(note_lines), new_link)


async def process_product(row: ProductRow, sheets: SheetsClient, client: EldoradoClient) -> None:
    cfg = RowConfig(row)
    if not cfg.is_checked_enabled:
        return  # bỏ qua thật sự im lặng — đây là trạng thái "cố ý tắt", không phải lỗi
    if not cfg.enabled:
        # Đã tích Enabled nhưng thiếu link bắt buộc — trước đây bị bỏ qua
        # HOÀN TOÀN im lặng (không ghi gì vào Status), khiến staff không
        # biết vì sao dòng không bao giờ chạy. Giờ báo lỗi rõ ràng.
        await _write_result(sheets, row.index, "Lỗi: đã tích Enabled nhưng thiếu My Listing URL hoặc Compare URL", None)
        return

    try:
        offer_id, offer_type = eldo.parse_offer_link(cfg.own_listing_url)
        if not offer_id:
            await _write_result(sheets, row.index, "Lỗi: không đọc được offer_id từ OWN_LISTING_URL", None)
            return

        try:
            offer, urls = await client.get_own_offer(offer_id, offer_type)
        except OfferNotFoundError:
            await _recover_missing_offer(row, cfg, sheets, client)
            return

        await _save_snapshot(row.index, cfg.own_listing_url, offer)

        new_price, new_stock, new_min_qty, note_lines = await _compute_target(cfg, offer, client)
        update_price = new_price != offer.price
        update_stock = new_stock != offer.quantity
        update_min_qty = new_min_qty != offer.min_quantity

        if not (update_price or update_stock or update_min_qty):
            note_lines.insert(0, "💤 Không có thay đổi.")
            await _write_result(sheets, row.index, "\n".join(note_lines), None)
            return

        if config.DRY_RUN:
            note_lines.insert(0, "[DRY RUN] Sẽ cập nhật (chưa ghi thật lên Eldorado).")
            await _write_result(sheets, row.index, "\n".join(note_lines), None)
            return

        only_price_changed = update_price and not update_stock and not update_min_qty
        success, message, link = await writer.apply_update(
            client, offer, urls, new_price, new_stock, new_min_qty,
            price_changed=update_price, only_price_changed=only_price_changed,
            allow_create_new=True, create_new_enabled=cfg.create_new_enabled,
        )
        if link:
            # apply_update trả link nghĩa là VỪA xoá + tạo lại offer mới do
            # 429 (xem writer.create_new_offer) — offer CŨ đã bị xoá thật.
            # Phải đánh dấu pending TRƯỚC khi ghi sheet, giống hệt luồng
            # phục hồi 404 ở _recover_missing_offer: nếu _write_result bên
            # dưới thất bại (kể cả sau khi đã retry), chu kỳ sau gặp 404 sẽ
            # thấy pending link này và KHÔNG tạo thêm 1 offer trùng nữa —
            # đây chính là bug thật đã gặp (dòng "Mirror COTA", 2026-09-13),
            # thiếu bước này thì luồng 404 vẫn có thể tạo offer trùng.
            await _mark_pending_link(row.index, link)
        note_lines.insert(0, ("✅ " if success else "❌ ") + message)
        await _write_result(sheets, row.index, "\n".join(note_lines), link)

    except EldoradoApiError as e:
        logger.error("[%s] Lỗi Eldorado API: %s", cfg.name, e)
        await _write_result(sheets, row.index, f"Lỗi: {e}", None)
    except Exception as e:
        logger.exception("[%s] Lỗi không mong đợi khi xử lý sản phẩm", cfg.name)
        await _write_result(sheets, row.index, f"Lỗi không xác định: {e}", None)
