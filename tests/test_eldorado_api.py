"""Test thuần cho eldorado_api.filter_competitors — riêng phần lọc theo từ
khoá tiêu đề (exclude_keywords/require_keywords), thêm lại theo yêu cầu
2026-09-15. Cũng test build_compare_url_from_sheet — đặc biệt việc filter
tradeEnvironmentValue/attribute LUÔN được chèn từ own offer, không phụ thuộc
link Compare URL dán vào sheet có mang theo te_v/attribute_value_id hay
không (bug thật 2026-09-17: link kiểu '/og/<uuid>?position=...' không mang
theo te_v, khiến compare bị trộn lẫn mọi biến thể khác trong cùng game)."""
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eldorado_api import build_compare_url_from_sheet, filter_competitors  # noqa: E402


def make_item(seller: str, price: str, title: str) -> dict:
    return {
        "user": {"username": seller},
        "offer": {"pricePerUnit": {"amount": price}, "quantity": 5, "offerTitle": title},
        "userOrderInfo": {"ratingCount": 100, "feedbackScore": 99},
    }


RAW = [
    make_item("A", "10", "Cheap Gold Bot Farm"),
    make_item("B", "11", "Manual Legit Gold"),
    make_item("C", "9", "Random Stuff"),
]


def _run(**kwargs):
    qualifying, _ = filter_competitors(
        RAW, blacklist=set(), min_stock=None, min_feedback=None, tile_feedback=None, floor_price=None, **kwargs
    )
    return sorted(m.seller for m in qualifying)


def test_filter_competitors_excludes_title_containing_any_exclude_keyword():
    assert _run(exclude_keywords={"bot"}, require_keywords=set()) == ["B", "C"]


def test_filter_competitors_keeps_only_titles_containing_a_require_keyword():
    assert _run(exclude_keywords=set(), require_keywords={"gold"}) == ["A", "B"]


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_build_compare_url_injects_trade_env_and_attribute_even_when_sheet_url_lacks_them():
    """Link kiểu '/og/<uuid>?position=3&offerSortingCriterion=Cheapest' (bug
    thật đã gặp) không mang theo te_v/attribute_value_id nào — trước đây kết
    quả là filter bị bỏ trống hoàn toàn. Giờ phải luôn lấy từ own offer."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/poe-currency/og/a9ca9a8f-...?position=3&offerSortingCriterion=Cheapest",
        game_id="2",
        category="Currency",
        trade_environment_values=[{"value": "PC"}, {"value": "Standard"}],
        attributes_raw=[{"id": "path-of-exile-orbs", "value": {"id": "mirror-of-kalandra"}}],
    )
    q = _query(url)
    assert q["tradeEnvironmentValue0"] == ["PC"]
    assert q["tradeEnvironmentValue1"] == ["Standard"]
    assert q["path-of-exile-orbs"] == ["mirror-of-kalandra"]
    assert q["offerSortingCriterion"] == ["Cheapest"]  # tham số khác của sheet URL vẫn giữ nguyên


def test_build_compare_url_own_offer_values_override_sheet_urls_stale_te_v():
    """Link sheet CÓ te_v/attribute_value_id (link kiểu cũ) nhưng giá trị đó
    có thể lỗi thời hoặc thuộc offer khác — own offer luôn là nguồn đáng tin
    cậy hơn, phải ghi đè chứ không cộng dồn/giữ giá trị cũ của URL."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/path-of-exile/currency?te_v0=Mac&te_v1=Hardcore&attribute_value_id=chaos-orb",
        game_id="2",
        category="Currency",
        trade_environment_values=[{"value": "PC"}, {"value": "Standard"}],
        attributes_raw=[{"id": "path-of-exile-orbs", "value": {"id": "mirror-of-kalandra"}}],
    )
    q = _query(url)
    assert q["tradeEnvironmentValue0"] == ["PC"]
    assert q["tradeEnvironmentValue1"] == ["Standard"]
    assert q["path-of-exile-orbs"] == ["mirror-of-kalandra"]
    assert "attribute_value_id" not in q  # tên tham số gốc của sheet URL không được lọt qua nguyên trạng


def test_build_compare_url_falls_back_to_sheet_te_v_when_own_offer_value_missing_at_index():
    """Own offer luôn ưu tiên, nhưng nếu trade_environment_values thiếu key
    "value" ở đúng 1 index (dạng đã xác nhận API có thể trả về — xem guard
    tương tự ở build_compare_url()), phải rơi về giá trị te_vN của chính
    sheet URL ở index đó thay vì bỏ trống hoàn toàn filter (bug đã bị agent
    review phát hiện ở bản vá đầu)."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/path-of-exile/currency?te_v0=PC&te_v1=Standard",
        game_id="2",
        category="Currency",
        trade_environment_values=[{"noValueKey": True}, {"value": "Hardcore"}],
        attributes_raw=None,
    )
    q = _query(url)
    assert q["tradeEnvironmentValue0"] == ["PC"]  # rơi về giá trị của sheet URL
    assert q["tradeEnvironmentValue1"] == ["Hardcore"]  # own offer có value -> vẫn ưu tiên own offer


def test_build_compare_url_falls_back_to_sheet_attribute_value_id_when_own_offer_has_none():
    """Own offer HOÀN TOÀN không có attributes (vd API đổi shape lần nữa,
    hoặc category không trả 'attributes') nhưng sheet URL kiểu cũ vẫn có
    ?attribute_value_id=... — phải rơi về giá trị đó thay vì mất luôn filter
    (bug đã bị agent review phát hiện)."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/path-of-exile/currency?attribute_value_id=mirror-of-kalandra",
        game_id="2",
        category="Currency",
        trade_environment_values=None,
        attributes_raw=None,
    )
    q = _query(url)
    assert q["path-of-exile"] == ["mirror-of-kalandra"]  # attr_key đoán từ path segment thứ 2 của URL


def test_build_compare_url_skips_attribute_filter_when_attribute_id_is_blank():
    """attributes_raw[0]["id"] rỗng ("") là response méo/bất thường — KHÔNG
    được rơi vào fallback đoán attr_key từ path URL (dễ tạo tham số API vô
    nghĩa, vd gửi thẳng tên game làm tên filter) — bug đã bị agent review
    phát hiện. Phải bỏ qua hẳn filter này, an toàn hơn là đoán bậy."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/path-of-exile/currency",
        game_id="2",
        category="Currency",
        trade_environment_values=None,
        attributes_raw=[{"id": "", "value": "mirror-of-kalandra"}],
    )
    q = _query(url)
    assert "currency" not in q
    assert "" not in q


def test_build_compare_url_prefers_own_offer_empty_value_over_stale_sheet_value():
    """Own offer có entry ở index đó (dù value rỗng "") vẫn phải THẮNG giá
    trị te_vN cũ của sheet URL — bug đã bị agent review phát hiện: dùng `or`
    khiến value rỗng bị coi như "không có", rơi nhầm về giá trị sheet cũ."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/path-of-exile/currency?te_v0=Mac",
        game_id="2",
        category="Currency",
        trade_environment_values=[{"value": ""}],
        attributes_raw=None,
    )
    q = _query(url)
    assert "tradeEnvironmentValue0" not in q  # own offer nói "rỗng" -> không lọc theo chiều đó, KHÔNG dùng "Mac" cũ


def test_build_compare_url_handles_attribute_value_as_bare_scalar_not_dict():
    """writer.py._build_offer_attributes chứng minh Eldorado API trả `value`
    dạng scalar thẳng (không bọc {"id": ...}) cho 1 số game/category — bug
    thật đã bị agent review phát hiện: bản vá đầu chỉ nhận dạng shape dict,
    bỏ qua scalar, làm mất filter attribute y hệt bug gốc."""
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/some-game/currency",
        game_id="9",
        category="Currency",
        trade_environment_values=[{"value": "PC"}],
        attributes_raw=[{"id": "denomination", "value": "chaos-orb"}],
    )
    q = _query(url)
    assert q["denomination"] == ["chaos-orb"]


def test_build_compare_url_skips_attribute_filter_when_own_offer_has_no_attributes():
    url = build_compare_url_from_sheet(
        sheet_url="https://www.eldorado.gg/some-game/currency",
        game_id="5",
        category="Currency",
        trade_environment_values=[{"value": "PC"}],
        attributes_raw=None,
    )
    q = _query(url)
    assert q["tradeEnvironmentValue0"] == ["PC"]
    assert "path-of-exile-orbs" not in q


def test_filter_competitors_combines_exclude_and_require():
    assert _run(exclude_keywords={"bot"}, require_keywords={"gold"}) == ["B"]


def test_filter_competitors_matches_title_case_insensitively_against_lowercased_keywords():
    """Hàm chỉ lowercase title, không lowercase keyword — RowConfig (caller)
    chịu trách nhiệm lowercase từ khoá trước khi truyền vào (xem
    product_pipeline._to_set)."""
    assert _run(exclude_keywords={"bot"}, require_keywords=set()) == ["B", "C"]


def test_filter_competitors_defaults_to_no_keyword_filtering():
    qualifying, _ = filter_competitors(
        RAW, blacklist=set(), min_stock=None, min_feedback=None, tile_feedback=None, floor_price=None
    )
    assert sorted(m.seller for m in qualifying) == ["A", "B", "C"]
