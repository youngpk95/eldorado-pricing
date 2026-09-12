"""Dataclass có kiểu thay cho các class 'bag of attributes' không kiểu của
bản gốc (SheetInfo/Result/TextUpdate/EldoCNLInfo/EldoCompare trong
product_models.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class ProductRow:
    """1 dòng thô đọc từ sheet cấu hình — mọi giá trị (stock/giá sàn/giá
    trần...) đều nằm THẲNG trong dòng này, KHÔNG còn tham chiếu chéo sang
    sheet khác như thiết kế ban đầu (đã bỏ theo yêu cầu — xem
    eldorado_repricer_project.md)."""

    index: int  # 0-based trong danh sách đã đọc, KHÔNG phải số dòng thật trên sheet
    raw: dict[str, str]

    def get(self, key: str, default: str = "") -> str:
        return self.raw.get(key, default) or default


@dataclass
class OwnOffer:
    """Chi tiết offer hiện tại của mình trên Eldorado (port từ EldoCNLInfo)."""

    offer_id: str
    offer_type: str
    category: str
    game_id: str
    price: Decimal
    quantity: int
    min_quantity: int
    description: str
    offer_title: str
    guaranteed_delivery_time: str
    delivery_method: str
    trade_environment_values: list[dict] = field(default_factory=list)
    attributes_raw: list[dict] = field(default_factory=list)
    raw_offer: dict[str, Any] = field(default_factory=dict)


@dataclass
class OfferUrls:
    detail: str
    compare: str
    update: str
    change: str


@dataclass
class CompetitorMatch:
    seller: str
    price: Decimal


@dataclass
class CompareResult:
    """Port từ EldoCompare — kết quả so đối thủ Eldorado + giá tính ra."""

    min_eldo_seller: str | None = None
    min_eldo_price: Decimal | None = None
    price_update: Decimal | None = None
    below_floor_competitors: list[CompetitorMatch] = field(default_factory=list)


@dataclass
class ProductResult:
    """Port từ Result + TextUpdate gộp lại — trạng thái xử lý xong 1 sản phẩm."""

    update_price: bool = False
    update_stock: bool = False
    update_time: bool = False
    update_min_qty: bool = False
    note_lines: list[str] = field(default_factory=list)
    new_offer_link: str | None = None
    error: str | None = None
