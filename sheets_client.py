"""Đọc/ghi Google Sheets — 1 sheet DUY NHẤT, không còn tham chiếu chéo sang
sheet khác (đã bỏ theo yêu cầu user 2026-09-13: mọi giá trị stock/giá sàn/giá
trần nằm THẲNG trong từng dòng sản phẩm, giống schema phẳng của tool G2G
Repricer sibling).

Đọc dữ liệu theo VỊ TRÍ CỘT (`sheet_schema.INTERNAL_KEYS`), KHÔNG theo chữ ở
dòng 1 — nhờ vậy dòng 1 có thể dùng nhãn tiếng Việt ngắn gọn cho nhân viên dễ
đọc mà không ảnh hưởng gì tới việc parse dữ liệu (xem sheet_schema.py)."""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
from sheet_schema import COLUMNS, INTERNAL_KEYS

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self) -> None:
        creds_dict = config.load_google_service_account()
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        # cache_discovery=False: tránh warning/ghi file cache không cần thiết
        # khi chạy như 1 script ngắn hạn (giống cách bản gốc tắt discovery cache).
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
        # httplib2.Http (nền tảng của googleapiclient) KHÔNG thread-safe —
        # read_config_rows/write_result chạy qua asyncio.to_thread và có thể
        # bị gọi song song (nhiều sản phẩm xử lý cùng lúc), 2 thread dùng
        # chung 1 kết nối TLS cùng lúc gây lỗi "SSL: DECRYPTION_FAILED_OR_
        # BAD_RECORD_MAC" ngẫu nhiên. Khoá tuần tự hoá mọi lời gọi API.
        self._lock = threading.Lock()

    def read_config_rows(self) -> list[dict[str, str]]:
        """Bỏ qua dòng 1 (chỉ là nhãn hiển thị) — map dữ liệu theo VỊ TRÍ cột
        khớp với sheet_schema.INTERNAL_KEYS, không theo chữ thật ở dòng 1.

        Lọc bỏ dòng "ma" (hoàn toàn trống nhưng vẫn được API trả về): dùng
        checkbox (Data Validation BOOLEAN) cho cột ENABLED khiến MỌI dòng
        trong vùng áp dụng tự có giá trị "FALSE" thay vì thật sự trống, nên
        vùng dữ liệu "đã dùng" của sheet bị kéo dài ra hàng trăm dòng trống —
        cùng bug thật đã gặp ở tool G2G Repricer sibling khi thêm checkbox
        (xem g2g_repricing_tool_feature.md). Chỉ giữ dòng có NAME hoặc
        OWN_LISTING_URL — KHÔNG dùng "mọi cell đều trống" để lọc, vì lý do
        trên."""
        with self._lock:
            data = (
                self._service.values()
                .get(spreadsheetId=config.SHEET_CONFIG_ID, range=config.CONFIG_RANGE)
                .execute()
            )
        values = data.get("values", [])
        if len(values) <= 1:
            return []
        data_rows = values[1:]
        name_idx = INTERNAL_KEYS.index("NAME")
        url_idx = INTERNAL_KEYS.index("OWN_LISTING_URL")
        rows = []
        for row in data_rows:
            has_name = name_idx < len(row) and row[name_idx]
            has_url = url_idx < len(row) and row[url_idx]
            if not has_name and not has_url:
                continue
            rows.append({INTERNAL_KEYS[i]: (row[i] if i < len(row) else "") for i in range(len(INTERNAL_KEYS))})
        return rows

    def _get_sheet_id(self) -> int:
        with self._lock:
            meta = self._service.get(spreadsheetId=config.SHEET_CONFIG_ID, fields="sheets.properties").execute()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == config.CONFIG_RANGE:
                return props["sheetId"]
        raise RuntimeError(f"Không tìm thấy tab '{config.CONFIG_RANGE}' trong spreadsheet {config.SHEET_CONFIG_ID}")

    def clear_range(self, a1_range: str) -> None:
        """Xoá sạch nội dung 1 vùng (giữ nguyên định dạng ô) — dùng khi cần
        dọn header/dữ liệu cũ trước khi thiết lập lại 1 tab config."""
        with self._lock:
            self._service.values().clear(spreadsheetId=config.SHEET_CONFIG_ID, range=a1_range).execute()

    def write_header_row_with_notes(self) -> None:
        """Ghi đè dòng 1 (header hiển thị) của tab config chính, kèm 1 note
        (chú thích, hiện khi rê chuột vào ô — icon tam giác đen góc phải) cho
        MỖI cột, giải thích cột đó dùng để làm gì. Nguồn dữ liệu:
        sheet_schema.COLUMNS — dùng lúc thiết lập tab lần đầu hoặc đổi lại
        nhãn/chú thích."""
        sheet_id = self._get_sheet_id()
        row_values = [
            {
                "userEnteredValue": {"stringValue": label},
                "note": note,
            }
            for _key, label, note in COLUMNS
        ]
        request = {
            "requests": [
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(row_values),
                        },
                        "rows": [{"values": row_values}],
                        "fields": "userEnteredValue,note",
                    }
                }
            ]
        }
        with self._lock:
            self._service.batchUpdate(spreadsheetId=config.SHEET_CONFIG_ID, body=request).execute()

    def add_header_column(self, column_index: int, label: str, note: str) -> None:
        """Ghi nhãn+chú thích cho ĐÚNG 1 cột header mới (0-based) — KHÔNG xoá
        hay đụng tới bất kỳ dữ liệu nào khác. Dùng khi thêm 1 cột mới vào
        schema đã có sẵn dữ liệu thật (khác `write_header_row_with_notes`,
        vốn ghi đè+xoá TOÀN BỘ tab, chỉ nên dùng lúc thiết lập tab hoàn toàn
        mới)."""
        sheet_id = self._get_sheet_id()
        request = {
            "requests": [
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": column_index,
                            "endColumnIndex": column_index + 1,
                        },
                        "rows": [{"values": [{"userEnteredValue": {"stringValue": label}, "note": note}]}],
                        "fields": "userEnteredValue,note",
                    }
                }
            ]
        }
        with self._lock:
            self._service.batchUpdate(spreadsheetId=config.SHEET_CONFIG_ID, body=request).execute()

    def set_checkbox_columns(self, column_indices: list[int], num_rows: int = 1000) -> None:
        """Đặt Data Validation kiểu BOOLEAN (checkbox thật) cho các cột
        boolean (0-based, vd ENABLED=0) từ dòng 2 trở đi — nhân viên tích/bỏ
        tích thay vì gõ tay '1'/'TRUE'. Gộp tất cả cột vào 1 batchUpdate
        duy nhất thay vì gọi lẻ từng cột."""
        sheet_id = self._get_sheet_id()
        requests = [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,  # bỏ qua dòng 1 (header)
                        "endRowIndex": 1 + num_rows,
                        "startColumnIndex": col,
                        "endColumnIndex": col + 1,
                    },
                    "rule": {
                        "condition": {"type": "BOOLEAN"},
                        "strict": True,
                        "showCustomUi": True,
                    },
                }
            }
            for col in column_indices
        ]
        with self._lock:
            self._service.batchUpdate(spreadsheetId=config.SHEET_CONFIG_ID, body={"requests": requests}).execute()

    def write_result(self, row_index: int, note: str, link: str | None) -> None:
        """Ghi 3 cột kết quả (Trạng thái/Cập nhật lúc/Link mới, cột C/D/E)
        đúng dòng — giữ hành vi ghi NGAY sau mỗi sản phẩm như bản gốc (để
        thấy tiến độ live), không gom hết tới cuối chu kỳ mới ghi 1 lần.

        Khi `link` có giá trị (chỉ xảy ra khi vừa xoá + tạo lại offer do gặp
        429 — xem product_pipeline.py), NGHĨA LÀ offer cũ đã bị xoá thật,
        nên cũng ghi đè luôn cột F (`My Listing URL`) sang link mới — nếu
        không, chu kỳ chạy SAU sẽ tiếp tục đọc link CŨ (đã bị xoá) và báo lỗi
        "Không tìm thấy ID sản phẩm" thay vì tiếp tục theo dõi đúng offer."""
        sheet_row = row_index + 2  # +1 header, +1 chuyển 0-based -> 1-based
        data = [
            {"range": f"{config.CONFIG_RANGE}!C{sheet_row}", "values": [[note]]},
            {"range": f"{config.CONFIG_RANGE}!D{sheet_row}", "values": [[datetime.now().strftime("%d/%m/%Y %H:%M:%S")]]},
            {"range": f"{config.CONFIG_RANGE}!E{sheet_row}", "values": [[link or ""]]},
        ]
        if link:
            data.append({"range": f"{config.CONFIG_RANGE}!F{sheet_row}", "values": [[link]]})
        try:
            with self._lock:
                self._service.values().batchUpdate(
                    spreadsheetId=config.SHEET_CONFIG_ID,
                    body={"valueInputOption": "USER_ENTERED", "data": data},
                ).execute()
        except Exception as e:
            logger.error("[sheets] Lỗi ghi kết quả dòng %s: %s", sheet_row, e)
