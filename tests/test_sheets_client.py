"""Test cho SheetsClient.read_config_rows — đặc biệt là việc lọc dòng "ma"
sau khi thêm checkbox (Data Validation BOOLEAN) cho cột ENABLED. Đây là bug
THẬT đã gặp ở tool G2G Repricer sibling (checkbox khiến mọi dòng trong vùng
áp dụng tự có giá trị "FALSE", kéo dài "vùng dữ liệu đã dùng" ra hàng trăm
dòng trống) — test này đảm bảo tool Eldorado không dính lại bug tương tự."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from sheet_schema import INTERNAL_KEYS  # noqa: E402
from sheets_client import SheetsClient  # noqa: E402


def _make_client_with_values(values: list[list[str]]) -> SheetsClient:
    client = SheetsClient.__new__(SheetsClient)  # bỏ qua __init__ thật (không cần Google credential)
    fake_service = MagicMock()
    fake_service.values.return_value.get.return_value.execute.return_value = {"values": values}
    client._service = fake_service
    return client


def test_read_config_rows_skips_phantom_checkbox_rows(monkeypatch):
    monkeypatch.setattr(config, "SHEET_CONFIG_ID", "dummy")
    monkeypatch.setattr(config, "CONFIG_RANGE", "Sheet1")

    header = ["Bật", "Tên SP"] + [""] * (len(INTERNAL_KEYS) - 2)
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

    client = _make_client_with_values([["Bật", "Tên SP"]])  # chỉ có header
    assert client.read_config_rows() == []
