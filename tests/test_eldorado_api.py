"""Test thuần cho eldorado_api.filter_competitors — riêng phần lọc theo từ
khoá tiêu đề (exclude_keywords/require_keywords), thêm lại theo yêu cầu
2026-09-15."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eldorado_api import filter_competitors  # noqa: E402


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
