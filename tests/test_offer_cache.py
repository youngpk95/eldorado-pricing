"""Test cho offer_cache.py — snapshot dùng để tự tạo lại offer khi gặp 404
(offer đã bị xoá). Bug thật đã gặp (2026-09-13, dòng "Mirror COTA"): ghi
link offer mới về sheet thất bại sau khi tạo lại do 429 -> chu kỳ sau vẫn
đọc link CŨ đã xoá -> 404 mãi mãi, không có cách nào tự hồi phục."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import offer_cache  # noqa: E402
from models import OwnOffer  # noqa: E402


def make_offer() -> OwnOffer:
    return OwnOffer(
        offer_id="abc123",
        offer_type="Currency",
        category="Currency",
        game_id="220",
        price=Decimal("0.0016"),
        quantity=458650,
        min_quantity=1500,
        description="Fast delivery",
        offer_title="Divine Orb",
        guaranteed_delivery_time="instant",
        delivery_method="",
        trade_environment_values=[{"id": "te1", "value": "Standard"}],
        attributes_raw=[],
        raw_offer={"gameId": "220", "category": "Currency", "attributes": [], "volumeDiscounts": []},
    )


def test_save_then_load_roundtrips_offer(monkeypatch, tmp_path):
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    url = "https://www.eldorado.gg/dashboard/offers/Currency/edit/abc123"

    offer_cache.save_snapshot(0, url, make_offer())
    result = offer_cache.load_snapshot(0, url)

    assert result is not None
    offer, pending_link = result
    assert offer.offer_id == "abc123"
    assert offer.price == Decimal("0.0016")  # Decimal phải round-trip đúng, không lệch do float
    assert offer.raw_offer == {"gameId": "220", "category": "Currency", "attributes": [], "volumeDiscounts": []}
    assert pending_link is None


def test_load_returns_none_when_url_mismatch(monkeypatch, tmp_path):
    """Sheet vừa bị sửa tay sang 1 offer khác hẳn (hoặc đây là snapshot của
    dòng khác) — KHÔNG được dùng nhầm snapshot cũ để tự tạo lại sai offer."""
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    offer_cache.save_snapshot(0, "https://www.eldorado.gg/dashboard/offers/Currency/edit/old", make_offer())

    result = offer_cache.load_snapshot(0, "https://www.eldorado.gg/dashboard/offers/Currency/edit/different")

    assert result is None


def test_load_returns_none_when_no_snapshot_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    assert offer_cache.load_snapshot(0, "https://anything") is None


def test_mark_pending_link_is_returned_by_load(monkeypatch, tmp_path):
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    url = "https://www.eldorado.gg/dashboard/offers/Currency/edit/abc123"
    offer_cache.save_snapshot(0, url, make_offer())

    offer_cache.mark_pending_link(0, "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id")
    _, pending_link = offer_cache.load_snapshot(0, url)

    assert pending_link == "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id"


def test_mark_pending_link_is_noop_without_prior_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    offer_cache.mark_pending_link(0, "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id")  # không được lỗi
    assert offer_cache.load_snapshot(0, "https://anything") is None


def test_save_snapshot_clears_previous_pending_link(monkeypatch, tmp_path):
    """Fetch offer thành công (200) nghĩa là sheet đã trỏ đúng offer sống —
    không còn gì "đang chờ phục hồi" nữa, phải xoá pending link cũ."""
    monkeypatch.setattr(offer_cache, "CACHE_DIR", tmp_path)
    url = "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id"
    offer_cache.save_snapshot(0, "https://old-url", make_offer())
    offer_cache.mark_pending_link(0, url)

    offer_cache.save_snapshot(0, url, make_offer())
    _, pending_link = offer_cache.load_snapshot(0, url)

    assert pending_link is None
