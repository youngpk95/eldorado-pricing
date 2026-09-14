"""Test cho SheetsClient.read_config_rows/write_result — đặc biệt việc đọc/
ghi theo TÊN HEADER ở dòng 1 thay vì vị trí cột (đổi từ 2026-09-14, sau bug
thật: nhân viên chèn cột mới vào GIỮA bảng làm lệch vị trí mọi cột phía sau,
code đọc/ghi nhầm cột mà không báo lỗi rõ — xem sheets_client.py docstring).
Cũng test việc lọc dòng "ma" sau khi thêm checkbox (Data Validation BOOLEAN)
cho cột ENABLED — bug THẬT đã gặp ở tool G2G Repricer sibling (checkbox
khiến mọi dòng trong vùng áp dụng tự có giá trị "FALSE", kéo dài "vùng dữ
liệu đã dùng" ra hàng trăm dòng trống)."""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from sheet_schema import COLUMNS, INTERNAL_KEYS  # noqa: E402
from sheets_client import SheetsClient, _column_letter, extract_spreadsheet_id  # noqa: E402

_HEADER_LABELS = [label for _key, label, _note in COLUMNS]


def _default_key_to_index() -> dict[str, int]:
    """Mapping key->index khớp ĐÚNG thứ tự khai báo trong sheet_schema.COLUMNS
    — dùng làm cache có sẵn cho các test write_result không quan tâm tới
    việc build mapping (đã có test riêng cho việc đó), tương đương hành vi
    C/D/E/F hardcode cũ vì LAST_STATUS/LAST_UPDATED_AT/NEW_OFFER_LINK/
    OWN_LISTING_URL đúng là cột thứ 3/4/5/6 trong schema mặc định."""
    return {key: i for i, key in enumerate(INTERNAL_KEYS)}


def _make_client_with_values(values: list[list[str]]) -> SheetsClient:
    client = SheetsClient.__new__(SheetsClient)  # bỏ qua __init__ thật (không cần Google credential)
    fake_service = MagicMock()
    fake_service.values.return_value.get.return_value.execute.return_value = {"values": values}
    client._service = fake_service
    client._lock = threading.Lock()
    client._key_to_index = None
    return client


def _make_write_client() -> tuple[SheetsClient, MagicMock]:
    client = SheetsClient.__new__(SheetsClient)
    fake_service = MagicMock()
    client._service = fake_service
    client._lock = threading.Lock()
    client._key_to_index = _default_key_to_index()  # bỏ qua bước build header — test riêng bên dưới
    return client, fake_service


def test_sheet_schema_columns_have_no_duplicate_keys_or_labels():
    """Bug thật vừa bị code review bắt được: sửa vị trí khai báo 1 cột trong
    COLUMNS (cắt/dán) dễ vô tình để sót bản CŨ, tạo ra 2 tuple trùng
    internal_key/label — _build_key_to_index() sẽ ÂM THẦM chỉ dùng bản cuối
    cùng (dict ghi đè), khiến cột xuất hiện ĐÚNG 2 lần trên sheet thật khi
    chạy setup_sheet_headers.py mà tool chỉ đọc/ghi đúng 1 trong 2 — y hệt
    lớp lỗi "đọc nhầm cột" mà cả đợt refactor này được viết ra để loại bỏ."""
    keys = [key for key, _label, _note in COLUMNS]
    labels = [label for _key, label, _note in COLUMNS]
    assert len(keys) == len(set(keys)), f"internal_key bị trùng trong sheet_schema.COLUMNS: {keys}"
    assert len(labels) == len(set(labels)), f"label bị trùng trong sheet_schema.COLUMNS: {labels}"


def test_column_letter_conversion():
    assert _column_letter(0) == "A"
    assert _column_letter(2) == "C"
    assert _column_letter(25) == "Z"
    assert _column_letter(26) == "AA"
    assert _column_letter(27) == "AB"


def test_read_config_rows_skips_phantom_checkbox_rows(monkeypatch):
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    header = list(_HEADER_LABELS)
    name_idx = INTERNAL_KEYS.index("NAME")
    url_idx = INTERNAL_KEYS.index("OWN_LISTING_URL")

    real_row = [""] * len(INTERNAL_KEYS)
    real_row[0] = "TRUE"
    real_row[name_idx] = "Sản phẩm thật"
    real_row[url_idx] = "https://www.eldorado.gg/dashboard/offers/Currency/edit/abc"

    # Dòng "ma": chỉ có checkbox=FALSE (do Data Validation áp cho cả vùng),
    # MỌI cột khác đều trống — phải bị lọc bỏ, không được coi là 1 sản phẩm.
    phantom_row = [""] * len(INTERNAL_KEYS)
    phantom_row[0] = "FALSE"

    values = [header, real_row, phantom_row, phantom_row, phantom_row]
    client = _make_client_with_values(values)

    rows = client.read_config_rows()

    assert len(rows) == 1
    assert rows[0]["NAME"] == "Sản phẩm thật"


def test_read_config_rows_empty_sheet_returns_empty_list(monkeypatch):
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    client = _make_client_with_values([["Bất kỳ", "Tiêu đề nào"]])  # chỉ có header, không cần khớp schema
    assert client.read_config_rows() == []


def test_read_config_rows_works_when_columns_reordered_on_sheet(monkeypatch):
    """Test regression cho chính bug thật đã gặp (2026-09-14): nhân viên
    chèn cột 'Discount' và 'Stock' đổi chỗ cho nhau trên sheet thật (khác
    thứ tự khai báo trong sheet_schema.COLUMNS) — code phải VẪN đọc đúng giá
    trị vào đúng key, vì giờ khớp theo TÊN header chứ không theo vị trí."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    header = list(_HEADER_LABELS)
    stock_idx = header.index("Stock")
    discount_idx = header.index("Discount")
    header[stock_idx], header[discount_idx] = header[discount_idx], header[stock_idx]  # đổi chỗ 2 cột

    row = [""] * len(header)
    name_idx = header.index("Name")
    url_idx = header.index("My Listing URL")
    row[name_idx] = "Sản phẩm test"
    row[url_idx] = "https://www.eldorado.gg/dashboard/offers/Currency/edit/abc"
    row[stock_idx] = "0.01"  # nằm ở VỊ TRÍ của "Discount" trên sheet, nhưng label ở đó thật ra là "Stock"
    row[discount_idx] = "999"  # nằm ở VỊ TRÍ của "Stock" trên sheet, nhưng label ở đó thật ra là "Discount"

    client = _make_client_with_values([header, row])
    rows = client.read_config_rows()

    assert len(rows) == 1
    assert rows[0]["STOCK"] == "999"  # lấy đúng theo TÊN "Stock", bất kể nó nằm ở vị trí nào
    assert rows[0]["DISCOUNT_AMOUNT"] == "0.01"  # lấy đúng theo TÊN "Discount"


def test_read_config_rows_raises_clear_error_when_required_header_missing(monkeypatch):
    """Đổi/xoá nhầm tên 1 header bắt buộc trên sheet thật (không khớp
    sheet_schema.COLUMNS nữa) phải báo lỗi RÕ RÀNG ngay khi đọc sheet, thay
    vì đọc nhầm dữ liệu cột khác rồi crash khó hiểu ở tận sâu bên trong logic
    tính giá (đúng như bug thật vừa gặp)."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    header = list(_HEADER_LABELS)
    header[header.index("Price Min")] = "Gia San"  # gõ nhầm/đổi tên không khớp schema

    row = [""] * len(header)
    client = _make_client_with_values([header, row])

    try:
        client.read_config_rows()
        assert False, "phải raise RuntimeError khi thiếu header bắt buộc"
    except RuntimeError as e:
        assert "Price Min" in str(e)
        assert "PRICE_MIN" in str(e)


def test_write_result_updates_own_listing_url_when_link_given(monkeypatch):
    """Sau khi xoá + tạo lại offer (429), offer CŨ đã mất — nếu không cập
    nhật luôn cột My Listing URL sang link mới, chu kỳ chạy sau sẽ tìm nhầm
    offer đã bị xoá. Xem product_pipeline.py/writer.py mục 429."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    client, fake_service = _make_write_client()

    client.write_result(0, "Đã xoá + tạo lại offer mới", "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id")

    body = fake_service.values.return_value.batchUpdate.call_args.kwargs["body"]
    ranges_written = {d["range"]: d["values"][0][0] for d in body["data"]}
    assert ranges_written["Sheet1!F2"] == "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id"
    assert ranges_written["Sheet1!E2"] == "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id"


def test_write_result_does_not_touch_own_listing_url_when_no_link(monkeypatch):
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    client, fake_service = _make_write_client()

    client.write_result(0, "Không có thay đổi", None)

    body = fake_service.values.return_value.batchUpdate.call_args.kwargs["body"]
    ranges_written = {d["range"] for d in body["data"]}
    assert "Sheet1!F2" not in ranges_written


def test_write_result_uses_actual_header_position_not_hardcoded_letters(monkeypatch):
    """Test regression trực tiếp cho phần NGUY HIỂM NHẤT của bug thật đã gặp
    (2026-09-14): nếu write vẫn hardcode C/D/E/F sau khi cột bị dịch chuyển
    trên sheet thật, tool sẽ GHI ĐÈ NHẦM lên dữ liệu cột khác. Ở đây
    'Status' bị dịch từ cột C sang cột Z (vd do chèn nhiều cột mới trước
    đó) — write_result PHẢI ghi đúng vào Z, không phải C."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    header = list(_HEADER_LABELS)
    header.remove("Status")
    header.append("Status")  # "Status" giờ là cột CUỐI CÙNG, không còn là cột C
    status_col_letter = _column_letter(len(header) - 1)
    assert status_col_letter != "C"

    client = _make_client_with_values([header, [""] * len(header)])
    client.read_config_rows()  # populate cache _key_to_index từ header THẬT ở trên

    fake_service = client._service
    client.write_result(0, "Trạng thái mới", None)

    body = fake_service.values.return_value.batchUpdate.call_args.kwargs["body"]
    ranges_written = {d["range"]: d["values"][0][0] for d in body["data"]}
    assert ranges_written[f"Sheet1!{status_col_letter}2"] == "Trạng thái mới"
    # Cột C giờ là 1 cột KHÁC (Status đã dịch ra cuối) — không được ghi nhầm
    # nội dung Status vào đó, dù "Sheet1!C2" vẫn được ghi (cho field khác).
    assert ranges_written.get("Sheet1!C2") != "Trạng thái mới"


def test_write_result_lazily_fetches_header_when_called_without_prior_read(monkeypatch):
    """write_result gọi ĐỘC LẬP (chưa từng read_config_rows trong chu kỳ
    này) vẫn phải hoạt động đúng — tự fetch riêng dòng 1 để build mapping,
    không AttributeError/crash."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    client = SheetsClient.__new__(SheetsClient)
    fake_service = MagicMock()
    fake_service.values.return_value.get.return_value.execute.return_value = {"values": [_HEADER_LABELS]}
    client._service = fake_service
    client._lock = threading.Lock()
    client._key_to_index = None

    client.write_result(0, "Trạng thái mới", None)

    get_call = fake_service.values.return_value.get.call_args
    assert get_call.kwargs["range"] == "Sheet1!1:1"  # chỉ fetch đúng dòng 1, không kéo hết data
    body = fake_service.values.return_value.batchUpdate.call_args.kwargs["body"]
    ranges_written = {d["range"]: d["values"][0][0] for d in body["data"]}
    assert ranges_written["Sheet1!C2"] == "Trạng thái mới"


def test_write_result_retries_then_succeeds(monkeypatch):
    """Lần ghi đầu 2 lần lỗi (vd tranh chấp kết nối/timeout), lần 3 thành
    công — không được bỏ cuộc ngay từ lần lỗi đầu tiên."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")
    monkeypatch.setattr(SheetsClient, "WRITE_RESULT_RETRY_DELAY_SECONDS", 0)

    client, fake_service = _make_write_client()
    fake_service.values.return_value.batchUpdate.return_value.execute.side_effect = [
        Exception("boom 1"),
        Exception("boom 2"),
        None,
    ]

    client.write_result(0, "Đã xoá + tạo lại offer mới", "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id")

    assert fake_service.values.return_value.batchUpdate.return_value.execute.call_count == 3


def test_extract_spreadsheet_id_from_full_url():
    url = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit#gid=123"
    assert extract_spreadsheet_id(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"


def test_extract_spreadsheet_id_accepts_bare_id():
    assert extract_spreadsheet_id("1AbCdEfGhIjKlMnOpQrStUvWxYz") == "1AbCdEfGhIjKlMnOpQrStUvWxYz"


def test_extract_spreadsheet_id_invalid_returns_none():
    assert extract_spreadsheet_id("") is None
    assert extract_spreadsheet_id("không phải url hợp lệ") is None


def test_read_external_cells_groups_by_spreadsheet_and_isolates_errors():
    """1 spreadsheet lỗi (vd 403 chưa share) không được làm hỏng việc đọc
    những spreadsheet KHÁC trong cùng 1 lần gọi — xem docstring
    read_external_cells trong sheets_client.py."""
    client = SheetsClient.__new__(SheetsClient)
    fake_service = MagicMock()

    def fake_batch_get(spreadsheetId, ranges):
        execute_mock = MagicMock()
        if spreadsheetId == "sheet-a":
            execute_mock.execute.return_value = {"valueRanges": [{"values": [["1.23"]]}]}
        else:
            execute_mock.execute.side_effect = Exception("403 chưa share quyền Viewer")
        return execute_mock

    fake_service.values.return_value.batchGet.side_effect = fake_batch_get
    client._service = fake_service
    client._lock = threading.Lock()

    refs = [("sheet-a", "Tab1", "B2"), ("sheet-b", "Tab1", "C3")]
    result = client.read_external_cells(refs)

    assert result[("sheet-a", "Tab1", "B2")] == "1.23"
    assert result[("sheet-b", "Tab1", "C3")] is None


def test_read_external_cells_empty_cell_returns_none():
    client = SheetsClient.__new__(SheetsClient)
    fake_service = MagicMock()
    fake_service.values.return_value.batchGet.return_value.execute.return_value = {
        "valueRanges": [{}]  # ô trống -> Sheets API không trả "values" luôn
    }
    client._service = fake_service
    client._lock = threading.Lock()

    result = client.read_external_cells([("sheet-a", "Tab1", "B2")])

    assert result[("sheet-a", "Tab1", "B2")] is None


def test_read_external_cells_error_log_mentions_max_and_cell_ref(caplog):
    """Bug phát hiện qua code review: log lỗi từng hardcode 'Lỗi đọc Price
    Min' dù method này giờ phục vụ CẢ Price Max — dòng chỉ bật Cell Max (để
    trống Cell Min) mà lỗi vẫn phải log rõ đang đọc ô nào, không được nói
    nhầm là đang đọc Price Min."""
    client = SheetsClient.__new__(SheetsClient)
    fake_service = MagicMock()
    fake_service.values.return_value.batchGet.return_value.execute.side_effect = Exception("403 chưa share")
    client._service = fake_service
    client._lock = threading.Lock()

    with caplog.at_level("WARNING"):
        client.read_external_cells([("sheet-a", "Tab1", "B3")])

    log_text = " ".join(r.message for r in caplog.records)
    assert "Tab1!B3" in log_text
    assert "Price Max" in log_text


def test_write_result_logs_link_when_all_retries_fail(monkeypatch, caplog):
    """Nếu ghi thất bại HẾT các lần thử mà có link (vừa tạo lại offer do
    429) — phải log RÕ link đó ra để còn dán tay, không được mất trắng."""
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")
    monkeypatch.setattr(SheetsClient, "WRITE_RESULT_RETRY_DELAY_SECONDS", 0)

    client, fake_service = _make_write_client()
    fake_service.values.return_value.batchUpdate.return_value.execute.side_effect = Exception("mất kết nối")

    with caplog.at_level("ERROR"):
        client.write_result(0, "Đã xoá + tạo lại offer mới", "https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id")

    assert fake_service.values.return_value.batchUpdate.return_value.execute.call_count == SheetsClient.WRITE_RESULT_MAX_ATTEMPTS
    assert any("https://www.eldorado.gg/dashboard/offers/Currency/edit/new-id" in r.message for r in caplog.records)
