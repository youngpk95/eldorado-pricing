"""Tự kiểm tra + tự cập nhật code khi nhánh GitHub đang theo dõi có commit
mới hơn HEAD local — dùng `git fetch`/`git rev-parse` để so sánh, rồi
`git pull --ff-only` khi có bản mới. Việc ĐÓNG kết nối đang mở (httpx client)
và `os.execv` khởi động lại process nằm ở main.py (nơi sở hữu event loop +
client, không phải ở đây) — module này chỉ có 2 hàm ĐỒNG BỘ (chạy qua
asyncio.to_thread từ main.py, giống cách sheets_client.py được gọi).

Chỉ được gọi ở ĐIỂM AN TOÀN trong main.py (sau khi 1 chu kỳ repricing đã
chạy xong hoàn toàn) — theo yêu cầu + xác nhận trực tiếp của user (tool
production, DRY_RUN có thể =False, đang chạy live, không được restart giữa
chừng lúc đang xử lý sản phẩm).

Dùng lại chính git credential đã cache sẵn trên máy (từ lần `git push` thủ
công đầu tiên lên repo private trên GitHub) — KHÔNG cần thêm GITHUB_TOKEN
vào .env, giữ thiết kế đơn giản như mọi phần khác của tool."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parent
GIT_TIMEOUT_SECONDS = 30


def _run_git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=True,
    )


def check_for_update(branch: str) -> bool:
    """True nếu origin/<branch> có commit khác HEAD local (mới hơn hoặc cũ
    hơn đều tính — thực tế chỉ nên khác khi có bản mới, vì tool không tự
    commit gì lên nhánh này). Không raise — lỗi mạng/git (vd máy tạm mất
    mạng) chỉ log warning và coi như KHÔNG có bản mới, để không làm crash
    vòng lặp chính ở main.py."""
    try:
        _run_git("fetch", "origin", branch, "--quiet")
        local = _run_git("rev-parse", "HEAD").stdout.strip()
        remote = _run_git("rev-parse", f"origin/{branch}").stdout.strip()
        return bool(local) and bool(remote) and local != remote
    except Exception as e:
        logger.warning("[updater] Không kiểm tra được bản cập nhật GitHub: %s", e)
        return False


def pull_update(branch: str) -> bool:
    """`git pull --ff-only` — dùng --ff-only để KHÔNG BAO GIỜ tự tạo merge
    commit hay đè conflict một cách âm thầm; nếu pull thất bại (vd có thay
    đổi local tại chỗ, hoặc lịch sử đã phân nhánh) thì log lỗi rõ ràng và trả
    False, để chu kỳ sau thử lại — KHÔNG restart khi chưa chắc code mới đã
    sẵn sàng."""
    try:
        _run_git("pull", "--ff-only", "origin", branch)
        logger.info("[updater] git pull --ff-only thành công (branch %s).", branch)
        return True
    except Exception as e:
        logger.error(
            "[updater] git pull thất bại (branch %s) — KHÔNG restart, thử lại chu kỳ sau: %s",
            branch, e,
        )
        return False
