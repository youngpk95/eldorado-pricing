"""Toàn bộ cấu hình đọc từ biến môi trường (.env) — không hardcode secret nào
trong code, khác với bản gốc (const.py có sẵn email/password/private key thật
dạng plaintext trong source)."""
import json
import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Thiếu biến môi trường bắt buộc: {name} (xem .env.example)")
    return value


ELDORADO_EMAIL = _get("ELDORADO_EMAIL", required=True)
ELDORADO_PASSWORD = _get("ELDORADO_PASSWORD", required=True)
ELDORADO_COGNITO_POOL_ID = _get("ELDORADO_COGNITO_POOL_ID", required=True)
ELDORADO_COGNITO_CLIENT_ID = _get("ELDORADO_COGNITO_CLIENT_ID", required=True)
ELDORADO_COGNITO_REGION = _get("ELDORADO_COGNITO_REGION", "us-east-2")

SHEET_CONFIG_ID = _get("SHEET_CONFIG_ID", required=True)
CONFIG_RANGE = _get("CONFIG_RANGE", required=True)


def load_google_service_account() -> dict:
    raw = _get("GOOGLE_SERVICE_ACCOUNT_JSON", required=True)
    # Cho phép truyền thẳng nội dung JSON, hoặc 1 đường dẫn tới file JSON
    # (tiện khi chạy local, giống cách g2gRepriceSheets.js hỗ trợ ở project
    # Node.js sibling).
    stripped = raw.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    with open(stripped, "r", encoding="utf-8") as f:
        return json.load(f)


CONCURRENCY_LIMIT = int(_get("CONCURRENCY_LIMIT", "5"))
LOOP_DELAY_SECONDS = float(_get("LOOP_DELAY_SECONDS", "30"))
PRODUCT_DELAY_SECONDS = float(_get("PRODUCT_DELAY_SECONDS", "1"))
ELDORADO_WRITE_DELAY_MS = float(_get("ELDORADO_WRITE_DELAY_MS", "300"))
ELDORADO_TIMEOUT_SECONDS = float(_get("ELDORADO_TIMEOUT_SECONDS", "20"))

DRY_RUN = _get("DRY_RUN", "true").strip().lower() != "false"

# Tự cập nhật từ GitHub (xem updater.py) — nhánh theo dõi + tần suất kiểm
# tra. Không cần GITHUB_TOKEN: dùng lại git credential đã cache sẵn trên máy.
GIT_BRANCH = _get("GIT_BRANCH", "master")
UPDATE_CHECK_INTERVAL_SECONDS = float(_get("UPDATE_CHECK_INTERVAL_SECONDS", "300"))
