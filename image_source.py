"""Tải + kiểm tra ảnh từ link (cột "Image URL" trên sheet) để upload lên
Eldorado khi tạo lại offer Item — ảnh KHÔNG cần nằm trong máy chạy tool, chỉ
cần 1 link tải được (link ảnh trực tiếp, hoặc link Google Drive chia sẻ
"Anyone with the link").

QUAN TRỌNG: dùng `httpx.AsyncClient` RIÊNG ở đây, KHÔNG dùng client của
Eldorado — client Eldorado luôn gắn Cookie đăng nhập vào mọi request, không
được phép gửi cookie đó sang 1 domain bên thứ ba (imgur, drive...)."""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

import config

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # giới hạn 10MB ghi trên trang upload của Eldorado

_DRIVE_FILE_RE = re.compile(r"drive\.google\.com/file/d/([\w-]+)")
_DRIVE_ID_PARAM_RE = re.compile(r"drive\.google\.com/(?:open|uc)\?(?:[^#]*&)?id=([\w-]+)")
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_PROP_RE = re.compile(r"""(?:property|name)\s*=\s*["']og:image["']""", re.I)
_META_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.I)
_MAX_HTML_BYTES = 2 * 1024 * 1024
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


class ImageError(Exception):
    """Không có được 1 ảnh hợp lệ để upload (link hỏng/không phải ảnh/quá lớn/
    upload thất bại) — người gọi PHẢI dừng, KHÔNG được xoá offer cũ."""


def normalize_image_url(url: str) -> str:
    """Đổi link chia sẻ Google Drive sang link tải trực tiếp; link khác giữ
    nguyên. Chỉ nhận http/https."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ImageError(f"Image URL không hợp lệ (phải bắt đầu bằng http/https): {url[:80]!r}")
    m = _DRIVE_FILE_RE.search(url) or _DRIVE_ID_PARAM_RE.search(url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def _is_ibb_host(host: str) -> bool:
    return host == "ibb.co" or host.endswith(".ibb.co")


def extract_og_image(html: str) -> str | None:
    """Lấy link ảnh thật từ thẻ <meta property="og:image"> của trang xem ảnh
    (chấp nhận thuộc tính property/content theo bất kỳ thứ tự nào)."""
    for tag in _META_TAG_RE.findall(html):
        if _META_PROP_RE.search(tag):
            m = _META_CONTENT_RE.search(tag)
            if m:
                return m.group(1).replace("&amp;", "&")
    return None


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    """Nhận diện theo NỘI DUNG file (không tin đuôi file/Content-Type) — link
    Drive không công khai trả về trang HTML đăng nhập với status 200, chỉ có
    kiểm tra nội dung mới phát hiện được. Trả về (đuôi, content-type) hoặc
    None nếu không phải JPEG/PNG/HEIC (3 định dạng Eldorado chấp nhận)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "heic", "image/heic"
    return None


async def _fetch_bytes(client: httpx.AsyncClient, url: str, limit: int, what: str) -> bytes:
    buf = bytearray()
    too_large = False
    async with client.stream("GET", url) as resp:
        if resp.status_code != 200:
            raise ImageError(f"Tải {what} thất bại (status {resp.status_code}) từ Image URL")
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > limit:
                too_large = True
                break  # thoát vòng lặp bình thường (không raise giữa chừng) để stream đóng gọn
    if too_large:
        raise ImageError("Ảnh quá lớn (> 10MB), Eldorado không nhận" if what == "ảnh" else f"Trang {what} quá lớn")
    return bytes(buf)


async def download_image(url: str, http: httpx.AsyncClient | None = None) -> tuple[bytes, str, str]:
    """Tải ảnh vào BỘ NHỚ (không ghi file). Trả về (bytes, tên file, content-type).
    Ném `ImageError` với lý do tiếng Việt nếu lỗi. `http` chỉ để test.

    Riêng link TRANG xem ảnh của ImgBB (`ibb.co/<id>`, không phải `i.ibb.co/...`
    là link ảnh trực tiếp): tự đọc thẻ `og:image` của trang để lấy link ảnh thật
    — CHỈ áp dụng cho domain ibb.co (và link lấy ra cũng phải thuộc ibb.co), vì
    tin `og:image` của trang bất kỳ có thể upload nhầm ảnh không liên quan."""
    target = normalize_image_url(url)
    own_client = http is None
    client = http or httpx.AsyncClient(timeout=config.ELDORADO_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        host = (urlparse(target).hostname or "").lower()
        if host in ("ibb.co", "www.ibb.co"):
            html = (await _fetch_bytes(client, target, _MAX_HTML_BYTES, "trang ImgBB")).decode("utf-8", "ignore")
            direct = extract_og_image(html)
            if not direct or not _is_ibb_host((urlparse(direct).hostname or "").lower()):
                raise ImageError("Không lấy được link ảnh từ trang ImgBB — hãy dán link ảnh trực tiếp (i.ibb.co/...)")
            target = direct
        data = await _fetch_bytes(client, target, MAX_IMAGE_BYTES, "ảnh")
    except ImageError:
        raise
    except (httpx.HTTPError, httpx.InvalidURL) as e:  # InvalidURL (URL méo) KHÔNG thuộc HTTPError
        raise ImageError(f"Không tải được ảnh từ Image URL: {type(e).__name__}") from e
    finally:
        if own_client:
            await client.aclose()

    detected = detect_image_type(data)
    if detected is None:
        raise ImageError(
            "File tải về không phải ảnh JPEG/PNG/HEIC (nếu dùng Google Drive: file phải chia sẻ 'Anyone with the link')"
        )
    ext, content_type = detected
    return data, f"image.{ext}", content_type
