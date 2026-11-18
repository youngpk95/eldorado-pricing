"""Toàn bộ cấu hình đọc từ biến môi trường (.env) — không hardcode secret nào
trong code, khác với bản gốc (const.py có sẵn email/password/private key thật
dạng plaintext trong source)."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
# Folder bỏ NGUYÊN VĂN file .json Service Account tải từ Google Cloud Console
# vào — xem load_google_service_account() bên dưới. Thêm cách này vì dán JSON
# thành 1 DÒNG DUY NHẤT vào .env (cách cũ) rất dễ làm hỏng file, cả nhân viên
# lẫn chính chủ đều khó tự làm đúng.
SERVICE_ACCOUNT_DIR = BASE_DIR / "service_account"


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Thiếu biến môi trường bắt buộc: {name} (xem .env.example)")
    return value


# Seller API CHÍNH THỨC của Eldorado (Dashboard > Seller API > Generate
# credentials, chỉ nick OWNER tạo được). Có đủ 2 biến này -> tool đăng nhập
# bằng token chính thức (eldo_auth.SellerApiAuth), KHÔNG cần email/mật khẩu
# nữa. Để trống -> đăng nhập kiểu cũ (Cognito SRP bằng email/mật khẩu).
# Lưu ý (test thật 2026-11-16): Seller API dùng CHUNG quota 429 với cả shop,
# không giúp thoát "Calm down, cowboy".
ELDORADO_SELLER_CLIENT_ID = _get("ELDORADO_SELLER_CLIENT_ID", "") or ""
ELDORADO_SELLER_CLIENT_SECRET = _get("ELDORADO_SELLER_CLIENT_SECRET", "") or ""
USE_SELLER_API = bool(ELDORADO_SELLER_CLIENT_ID.strip() and ELDORADO_SELLER_CLIENT_SECRET.strip())

ELDORADO_EMAIL = _get("ELDORADO_EMAIL", required=not USE_SELLER_API)
ELDORADO_PASSWORD = _get("ELDORADO_PASSWORD", required=not USE_SELLER_API)
ELDORADO_COGNITO_POOL_ID = _get("ELDORADO_COGNITO_POOL_ID", required=not USE_SELLER_API)
ELDORADO_COGNITO_CLIENT_ID = _get("ELDORADO_COGNITO_CLIENT_ID", required=not USE_SELLER_API)
ELDORADO_COGNITO_REGION = _get("ELDORADO_COGNITO_REGION", "us-east-2")

SHEET_CONFIG_ID = _get("SHEET_CONFIG_ID", required=True)
CONFIG_RANGE = _get("CONFIG_RANGE", required=True)


def load_google_service_account() -> dict:
    """Đọc credential Google Service Account, ưu tiên theo thứ tự:

    1. Biến môi trường `GOOGLE_SERVICE_ACCOUNT_JSON` trong `.env` — nếu có,
       chấp nhận cả nội dung JSON dán thẳng (1 dòng) LẪN 1 đường dẫn tới file
       .json (cách cũ, vẫn giữ để không phá ai đang dùng kiểu này).
    2. MẶC ĐỊNH/khuyến khích: bỏ NGUYÊN VĂN (không sửa gì) file .json tải từ
       Google Cloud Console vào folder `service_account/` cạnh file này — tool
       tự tìm và dùng file .json duy nhất trong đó, không cần convert/dán vào
       `.env` — xem `service_account/README.txt`."""
    raw = _get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        stripped = raw.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
        with open(stripped, "r", encoding="utf-8") as f:
            return json.load(f)

    json_files = sorted(SERVICE_ACCOUNT_DIR.glob("*.json")) if SERVICE_ACCOUNT_DIR.is_dir() else []
    if len(json_files) == 1:
        with open(json_files[0], "r", encoding="utf-8") as f:
            return json.load(f)
    if len(json_files) > 1:
        names = ", ".join(f.name for f in json_files)
        raise RuntimeError(
            f"Có {len(json_files)} file .json trong folder 'service_account/' ({names}) — chỉ được để "
            f"ĐÚNG 1 file. Xoá bớt file thừa, hoặc điền GOOGLE_SERVICE_ACCOUNT_JSON trong .env để chỉ rõ "
            f"dùng file nào."
        )
    raise RuntimeError(
        "Thiếu credential Google Service Account: bỏ NGUYÊN VĂN file .json tải từ Google Cloud Console vào "
        "folder 'service_account/' (xem service_account/README.txt), hoặc điền GOOGLE_SERVICE_ACCOUNT_JSON "
        "trong .env (xem .env.example)."
    )


CONCURRENCY_LIMIT = int(_get("CONCURRENCY_LIMIT", "5"))
LOOP_DELAY_SECONDS = float(_get("LOOP_DELAY_SECONDS", "30"))
PRODUCT_DELAY_SECONDS = float(_get("PRODUCT_DELAY_SECONDS", "1"))
ELDORADO_WRITE_DELAY_MS = float(_get("ELDORADO_WRITE_DELAY_MS", "300"))
# TẠO offer bị 429 -> chờ tăng dần trước lần tạo kế tiếp (mọi dòng dùng chung):
# BASE, 2xBASE, 4xBASE... tối đa MAX; tạo thành công 1 lần là xoá chờ ngay.
# Eldorado gửi Retry-After thì làm theo Retry-After. Xem writer._post_new_offer.
CREATE_BACKOFF_BASE_SECONDS = float(_get("ELDORADO_CREATE_BACKOFF_BASE_SECONDS", "60"))
CREATE_BACKOFF_MAX_SECONDS = float(_get("ELDORADO_CREATE_BACKOFF_MAX_SECONDS", "900"))
# BẤT KỲ lệnh GHI nào (đổi giá/sửa/tạo) bị 429 -> MỌI dòng tạm dừng ghi:
# BASE, 2xBASE, 4xBASE... tối đa MAX; ghi thành công 1 lần là xoá chờ ngay.
# Tránh việc dòng nào/chu kỳ nào cũng gửi lệnh rồi lại nhận 429 (2026-11-16).
WRITE_PAUSE_BASE_SECONDS = float(_get("ELDORADO_WRITE_PAUSE_BASE_SECONDS", "60"))
WRITE_PAUSE_MAX_SECONDS = float(_get("ELDORADO_WRITE_PAUSE_MAX_SECONDS", "900"))
# Khoảng cách tối thiểu giữa 2 lần TẠO offer (mọi dòng dùng chung) — tool tạo
# dồn dập (2 offer cách nhau <1s mỗi chu kỳ) là nguyên nhân bị chặn 2026-11-14.
# 5s theo yêu cầu user (thao tác tay xoá rồi tạo lại có độ trễ vẫn không bị chặn).
CREATE_MIN_INTERVAL_SECONDS = float(_get("ELDORADO_CREATE_MIN_INTERVAL_SECONDS", "5"))
# Chờ giữa bước XOÁ offer cũ và bước TẠO offer mới (theo cả 2 thứ tự), giống
# thao tác tay — không bắn DELETE + POST liền nhau trong 0.3s.
RECREATE_STEP_DELAY_SECONDS = float(_get("ELDORADO_RECREATE_STEP_DELAY_SECONDS", "5"))
# minQty tính lại theo giá mỗi chu kỳ -> nhích 1 bước là phải gửi PUT /details nặng thay vì
# changePrice nhẹ. Giữ minQty cũ nếu nó vẫn đạt yêu cầu và chỉ cao hơn mức tính mới không quá
# tỉ lệ này (0.20 = 20%). Đặt 0 để tắt (luôn cập nhật theo giá).
MIN_QTY_TOLERANCE = float(_get("ELDORADO_MIN_QTY_TOLERANCE", "0.20"))
ELDORADO_TIMEOUT_SECONDS = float(_get("ELDORADO_TIMEOUT_SECONDS", "20"))

DRY_RUN = _get("DRY_RUN", "true").strip().lower() != "false"

# Tự cập nhật từ GitHub (xem updater.py) — nhánh theo dõi + tần suất kiểm
# tra. Không cần GITHUB_TOKEN: dùng lại git credential đã cache sẵn trên máy.
GIT_BRANCH = _get("GIT_BRANCH", "master")
UPDATE_CHECK_INTERVAL_SECONDS = float(_get("UPDATE_CHECK_INTERVAL_SECONDS", "300"))

# Ô (1 ô DUY NHẤT, trên CHÍNH tab CONFIG_RANGE — không phải tab riêng) hiển
# thị version tool (đếm commit + hash ngắn) + trạng thái tự cập nhật (OK /
# lỗi kiểm tra / lỗi pull) + giờ ghi gần nhất — mở sheet của bất kỳ máy nào
# là biết ngay máy đó đang chạy bản nào, không cần log cục bộ (xem
# main._write_version_status). Mặc định "AC1": sheet_schema.COLUMNS hiện
# chiếm đúng cột A..AA (27 cột) — AC1 chừa dư 1 cột trống (AB) làm khoảng
# cách, ở dòng 1 (dễ thấy ngay khi mở sheet), không đụng dữ liệu sản phẩm
# nào kể cả khi thêm vài cột COLUMNS mới liền kề AA.
VERSION_CELL = _get("VERSION_CELL", "AC1")
