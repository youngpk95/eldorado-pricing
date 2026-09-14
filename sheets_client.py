"""Đọc/ghi Google Sheets — sheet cấu hình chính vẫn là 1 sheet DUY NHẤT,
không tham chiếu chéo tràn lan như thiết kế ban đầu (đã bỏ theo yêu cầu user
2026-09-13: mọi giá trị stock/giá sàn/giá trần nằm THẲNG trong từng dòng sản
phẩm, giống schema phẳng của tool G2G Repricer sibling).

NGOẠI LỆ (thêm sau, theo yêu cầu khác của user): khi Price Min và/hoặc Price
Max thật sự nằm ở 1 sheet KHÁC, `read_external_cells()` bên dưới đọc TRỰC
TIẾP qua Sheets API (không phải IMPORTRANGE, không bị lag) — chỉ áp dụng cho
đúng 2 giá trị Price Min/Price Max, chỉ kích hoạt khi dòng đó chủ động điền
cột 'Link Sheet'/'Name Sheet' + 'Cell Min' và/hoặc 'Cell Max', khác hẳn cơ
chế tham chiếu chéo toàn diện đã bỏ.

Đọc/ghi dữ liệu theo TÊN HEADER ở dòng 1 (khớp CHÍNH XÁC với label trong
`sheet_schema.COLUMNS`), KHÔNG theo vị trí cột nữa. Đổi hẳn từ vị trí sang
tên sau bug thật đã gặp (2026-09-14): nhân viên chèn 4 cột mới (Link
Sheet/Name Sheet/Cell Min/Cell Max) vào GIỮA bảng thay vì cuối bảng, làm
lệch vị trí MỌI cột phía sau — code đọc/ghi nhầm cột mà không hề báo lỗi rõ
ràng (chỉ crash sâu bên trong logic tính giá với `ValueError` khó hiểu:
"could not convert string to float: 'TRUE'", vì đọc nhầm giá trị checkbox
Always Undercut vào chỗ Min Competitor Ratings). Đọc/ghi theo tên header thì
chèn/xoá/đổi thứ tự cột bất kỳ đâu trên sheet đều KHÔNG làm hỏng gì (miễn
KHÔNG đổi tên/xoá nhầm chính header đó) — đổi lại: đổi TÊN header ở dòng 1
giờ mới là thứ không được làm tuỳ tiện (phải khớp `sheet_schema.COLUMNS`),
ngược hẳn với thiết kế cũ (trước đây đổi tên thoải mái, đổi vị trí thì vỡ)."""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
from sheet_schema import COLUMNS, INTERNAL_KEYS

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def extract_spreadsheet_id(url: str) -> str | None:
    """Lấy spreadsheet ID từ URL Google Sheets dạng
    `.../spreadsheets/d/<ID>/edit#gid=...` — dùng cho cột 'Link Sheet' (đọc
    Price Min/Price Max trực tiếp từ sheet KHÁC, xem read_external_cells bên
    dưới). Cũng chấp nhận trường hợp người dùng dán thẳng spreadsheet ID
    (không phải URL đầy đủ) để đỡ phải bắt lỗi nhập sai định dạng."""
    if not url:
        return None
    match = _SPREADSHEET_ID_RE.search(url)
    if match:
        return match.group(1)
    stripped = url.strip()
    if stripped and "/" not in stripped and " " not in stripped:
        return stripped
    return None


def _column_letter(index: int) -> str:
    """0-based column index -> ký hiệu cột kiểu A1 (0->A, 25->Z, 26->AA...)."""
    letters = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


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
        # Map internal_key -> 0-based column index THẬT trên sheet, build từ
        # header dòng 1 (xem _build_key_to_index) — cache lại trong
        # read_config_rows() mỗi chu kỳ, write_result() dùng lại cache này để
        # biết đúng cột thật cần ghi (không hardcode C/D/E/F nữa).
        self._key_to_index: dict[str, int] | None = None

    def _build_key_to_index(self, header_row: list[str]) -> dict[str, int]:
        """Map internal_key -> 0-based column index bằng cách khớp TÊN header
        thật ở dòng 1 với label trong sheet_schema.COLUMNS — bất kể cột đó
        đang nằm ở vị trí nào trên sheet. Thiếu/đổi tên nhầm 1 header bắt
        buộc thì báo lỗi rõ ràng NGAY ĐÂY, thay vì để code đọc nhầm dữ liệu
        cột khác rồi crash khó hiểu ở tận sâu trong logic tính giá."""
        label_to_index = {label.strip(): i for i, label in enumerate(header_row) if label.strip()}
        key_to_index: dict[str, int] = {}
        missing: list[str] = []
        for key, label, _note in COLUMNS:
            if label in label_to_index:
                key_to_index[key] = label_to_index[label]
            else:
                missing.append(f"'{label}' ({key})")
        if missing:
            raise RuntimeError(
                f"Dòng 1 của sheet cấu hình thiếu {len(missing)} cột bắt buộc (tên không khớp — có thể bị "
                f"đổi tên hoặc xoá nhầm): {', '.join(missing)}. Tool đọc/ghi theo ĐÚNG TÊN header ở dòng 1 "
                f"(không theo vị trí) — sửa lại tên cột cho khớp, xem sheet_schema.py."
            )
        return key_to_index

    def _ensure_key_to_index(self) -> dict[str, int]:
        """Trả cache đã build trong lần read_config_rows() gần nhất (trường
        hợp bình thường: main.py luôn đọc 1 lần rồi mới ghi nhiều lần/chu
        kỳ). Nếu chưa có cache (vd write_result được gọi độc lập, chưa từng
        read — chỉ xảy ra khi dùng SheetsClient lẻ tẻ ngoài luồng chính),
        fetch riêng đúng dòng 1 để build."""
        if self._key_to_index is not None:
            return self._key_to_index
        with self._lock:
            data = (
                self._service.values()
                .get(spreadsheetId=config.SHEET_CONFIG_ID, range=f"{config.CONFIG_RANGE}!1:1")
                .execute()
            )
        header_row = data.get("values", [[]])[0] if data.get("values") else []
        self._key_to_index = self._build_key_to_index(header_row)
        return self._key_to_index

    def read_config_rows(self) -> list[dict[str, str]]:
        """Đọc theo TÊN header dòng 1 (xem _build_key_to_index), không theo
        vị trí — chèn/xoá/đổi thứ tự cột trên sheet không làm hỏng gì, miễn
        không đổi tên/xoá nhầm chính các header đó.

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
        key_to_index = self._build_key_to_index(values[0])
        self._key_to_index = key_to_index  # cache cho write_result() dùng lại trong chu kỳ này

        data_rows = values[1:]
        name_idx = key_to_index["NAME"]
        url_idx = key_to_index["OWN_LISTING_URL"]
        rows = []
        for row in data_rows:
            has_name = name_idx < len(row) and row[name_idx]
            has_url = url_idx < len(row) and row[url_idx]
            if not has_name and not has_url:
                continue
            rows.append({key: (row[idx] if idx < len(row) else "") for key, idx in key_to_index.items()})
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

    def read_external_cells(
        self, refs: list[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], str | None]:
        """Đọc Price Min/Price Max TRỰC TIẾP qua Sheets API từ 1 hay nhiều
        sheet KHÁC sheet cấu hình chính (cột 'Link Sheet'/'Name Sheet'/'Cell
        Min'/'Cell Max') — thay cho công thức IMPORTRANGE, vốn bị delay/lag
        không cập nhật kịp thời (yêu cầu người dùng, không phải cơ chế tham
        chiếu chéo cũ đã bỏ 2026-09-13 — cơ chế cũ đọc MỌI giá trị chéo sheet;
        cái này CHỈ đọc đúng 2 giá trị Price Min/Price Max, và CHỈ khi người
        dùng chủ động điền các cột trên).

        `refs` là danh sách (spreadsheet_id, sheet_name, cell) cần đọc, gom 1
        LẦN cho cả chu kỳ (xem main._resolve_external_cells) — không đọc
        riêng từng dòng để tránh gọi API nhiều lần/dễ vượt quota.

        Gom theo spreadsheet_id: mỗi spreadsheet ID chỉ gọi ĐÚNG 1 lần
        `values().batchGet()` cho mọi cell cần đọc trong đó. 1 spreadsheet
        lỗi (thường do CHƯA share quyền Viewer cho service account, sai tên
        tab, hoặc sai ô) không được làm hỏng việc đọc các spreadsheet khác —
        log cảnh báo riêng, trả None cho đúng những ref thuộc spreadsheet đó."""
        result: dict[tuple[str, str, str], str | None] = {}
        by_spreadsheet: dict[str, list[tuple[str, str, str]]] = {}
        for ref in refs:
            by_spreadsheet.setdefault(ref[0], []).append(ref)

        for spreadsheet_id, spreadsheet_refs in by_spreadsheet.items():
            ranges = [f"'{sheet_name}'!{cell}" for _sid, sheet_name, cell in spreadsheet_refs]
            try:
                with self._lock:
                    data = (
                        self._service.values()
                        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
                        .execute()
                    )
                value_ranges = data.get("valueRanges", [])
                for ref, value_range in zip(spreadsheet_refs, value_ranges):
                    values = value_range.get("values")
                    result[ref] = values[0][0] if values and values[0] else None
            except Exception as e:
                cells = ", ".join(f"{sheet_name}!{cell}" for _sid, sheet_name, cell in spreadsheet_refs)
                logger.warning(
                    "[sheets] Lỗi đọc Price Min/Price Max từ sheet ngoài (spreadsheetId=%s, ô: %s): %s — "
                    "kiểm tra đã share quyền Viewer cho service account, đúng tên tab và đúng ô chưa.",
                    spreadsheet_id, cells, e,
                )
                for ref in spreadsheet_refs:
                    result[ref] = None

        return result

    WRITE_RESULT_MAX_ATTEMPTS = 3
    WRITE_RESULT_RETRY_DELAY_SECONDS = 2

    def write_result(self, row_index: int, note: str, link: str | None) -> None:
        """Ghi 3 cột kết quả (Status/Updated At/New Link) đúng dòng — giữ
        hành vi ghi NGAY sau mỗi sản phẩm như bản gốc (để thấy tiến độ
        live), không gom hết tới cuối chu kỳ mới ghi 1 lần.

        Cột đích tra theo TÊN header (qua _ensure_key_to_index), KHÔNG còn
        hardcode C/D/E/F — bug thật đã gặp (2026-09-14): nhân viên chèn cột
        mới vào giữa bảng làm lệch vị trí, nếu vẫn hardcode chữ cột thì write
        sẽ ghi ĐÈ NHẦM lên dữ liệu cột khác (còn nguy hiểm hơn cả đọc nhầm).

        Khi `link` có giá trị (chỉ xảy ra khi vừa xoá + tạo lại offer do gặp
        429 — xem product_pipeline.py), NGHĨA LÀ offer cũ đã bị xoá thật,
        nên cũng ghi đè luôn cột `My Listing URL` sang link mới — nếu không,
        chu kỳ chạy SAU sẽ tiếp tục đọc link CŨ (đã bị xoá) và báo lỗi
        "Không tìm thấy ID sản phẩm" thay vì tiếp tục theo dõi đúng offer.

        Bug thật đã gặp (2026-09-13): ghi thất bại ngay sau khi tạo lại offer
        mới khiến dòng đó KẸT VĨNH VIỄN (offer cũ đã xoá thật, sheet vẫn trỏ
        về ID cũ, tool không có cách nào tự phát hiện lại offer mới) — phải
        sửa tay. Nên giờ retry vài lần trước khi bỏ cuộc; nếu vẫn thất bại và
        có `link` (trường hợp tốn kém nhất), log RÕ link đó ra để còn dán tay
        được, thay vì mất luôn không dấu vết."""
        sheet_row = row_index + 2  # +1 header, +1 chuyển 0-based -> 1-based
        key_to_index = self._ensure_key_to_index()
        status_col = _column_letter(key_to_index["LAST_STATUS"])
        updated_col = _column_letter(key_to_index["LAST_UPDATED_AT"])
        newlink_col = _column_letter(key_to_index["NEW_OFFER_LINK"])
        ownurl_col = _column_letter(key_to_index["OWN_LISTING_URL"])
        data = [
            {"range": f"{config.CONFIG_RANGE}!{status_col}{sheet_row}", "values": [[note]]},
            {"range": f"{config.CONFIG_RANGE}!{updated_col}{sheet_row}", "values": [[datetime.now().strftime("%d/%m/%Y %H:%M:%S")]]},
            {"range": f"{config.CONFIG_RANGE}!{newlink_col}{sheet_row}", "values": [[link or ""]]},
        ]
        if link:
            data.append({"range": f"{config.CONFIG_RANGE}!{ownurl_col}{sheet_row}", "values": [[link]]})

        last_error: Exception | None = None
        for attempt in range(1, self.WRITE_RESULT_MAX_ATTEMPTS + 1):
            try:
                with self._lock:
                    self._service.values().batchUpdate(
                        spreadsheetId=config.SHEET_CONFIG_ID,
                        body={"valueInputOption": "USER_ENTERED", "data": data},
                    ).execute()
                return
            except Exception as e:
                last_error = e
                if attempt < self.WRITE_RESULT_MAX_ATTEMPTS:
                    logger.warning(
                        "[sheets] Ghi kết quả dòng %s lỗi (lần %d/%d), thử lại: %s",
                        sheet_row, attempt, self.WRITE_RESULT_MAX_ATTEMPTS, e,
                    )
                    time.sleep(self.WRITE_RESULT_RETRY_DELAY_SECONDS)

        logger.error(
            "[sheets] Lỗi ghi kết quả dòng %s (đã thử %d lần): %s",
            sheet_row, self.WRITE_RESULT_MAX_ATTEMPTS, last_error,
        )
        if link:
            logger.error(
                "[sheets] QUAN TRỌNG: dòng %s vừa tạo lại offer mới nhưng KHÔNG ghi được link vào sheet "
                "— offer CŨ đã bị xoá thật, phải tự dán link này vào cột 'My Listing URL' (cột %s): %s",
                sheet_row, ownurl_col, link,
            )
