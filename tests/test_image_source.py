"""Test tải + kiểm tra ảnh (image_source) và parse response upload của
Eldorado — giả lập HTTP bằng httpx.MockTransport, KHÔNG gọi mạng thật."""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import image_source  # noqa: E402
from eldorado_api import EldoradoApiError, parse_uploaded_image_paths  # noqa: E402
from image_source import ImageError, detect_image_type, download_image, normalize_image_url  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_normalize_converts_drive_share_links_to_direct_download():
    expected = "https://drive.google.com/uc?export=download&id=ABC_def-123"
    assert normalize_image_url("https://drive.google.com/file/d/ABC_def-123/view?usp=sharing") == expected
    assert normalize_image_url("https://drive.google.com/open?id=ABC_def-123") == expected


def test_normalize_keeps_direct_links_and_rejects_non_http():
    assert normalize_image_url("  https://i.imgur.com/x.png ") == "https://i.imgur.com/x.png"
    with pytest.raises(ImageError):
        normalize_image_url("C:\\images\\a.png")
    with pytest.raises(ImageError):
        normalize_image_url("")


def test_detect_image_type_by_content_not_extension():
    assert detect_image_type(PNG) == ("png", "image/png")
    assert detect_image_type(JPEG) == ("jpg", "image/jpeg")
    assert detect_image_type(HEIC) == ("heic", "image/heic")
    assert detect_image_type(b"<!DOCTYPE html><html>") is None


@pytest.mark.asyncio
async def test_download_image_success_returns_bytes_and_type():
    async with make_client(lambda req: httpx.Response(200, content=PNG)) as http:
        data, filename, content_type = await download_image("https://x.test/a.png", http)
    assert data == PNG
    assert filename == "image.png"
    assert content_type == "image/png"


@pytest.mark.asyncio
async def test_download_image_rejects_html_page_served_with_200():
    """Link Drive không công khai trả trang đăng nhập HTML với status 200."""
    async with make_client(lambda req: httpx.Response(200, content=b"<html>Sign in</html>")) as http:
        with pytest.raises(ImageError, match="không phải ảnh"):
            await download_image("https://drive.google.com/file/d/abc/view", http)


@pytest.mark.asyncio
async def test_download_image_rejects_http_error_status():
    async with make_client(lambda req: httpx.Response(404)) as http:
        with pytest.raises(ImageError, match="404"):
            await download_image("https://x.test/missing.png", http)


@pytest.mark.asyncio
async def test_download_image_rejects_too_large(monkeypatch):
    monkeypatch.setattr(image_source, "MAX_IMAGE_BYTES", 16)
    async with make_client(lambda req: httpx.Response(200, content=PNG)) as http:
        with pytest.raises(ImageError, match="quá lớn"):
            await download_image("https://x.test/big.png", http)


@pytest.mark.asyncio
async def test_download_image_network_error_becomes_image_error():
    def boom(req):
        raise httpx.ConnectError("dns fail")

    async with make_client(boom) as http:
        with pytest.raises(ImageError, match="Không tải được"):
            await download_image("https://x.test/a.png", http)


def test_parse_uploaded_image_paths_strips_prefix_and_maps_sizes():
    """Response thật bắt từ Eldorado 2026-09-20 (đổi số cho gọn)."""
    paths = [
        "/offerimages/google-oauth2|1109_Offer_20260920143958_1Small.png",
        "/offerimages/google-oauth2|1109_Offer_20260920143958_2Large.png",
        "/offerimages/google-oauth2|1109_Offer_20260920143958_3Original.png",
    ]
    assert parse_uploaded_image_paths(paths) == {
        "smallImage": "google-oauth2|1109_Offer_20260920143958_1Small.png",
        "largeImage": "google-oauth2|1109_Offer_20260920143958_2Large.png",
        "originalSizeImage": "google-oauth2|1109_Offer_20260920143958_3Original.png",
    }


def test_parse_uploaded_image_paths_raises_when_a_size_is_missing():
    with pytest.raises(EldoradoApiError):
        parse_uploaded_image_paths(["/offerimages/a_1Small.png", "/offerimages/a_2Large.png"])
    with pytest.raises(EldoradoApiError):
        parse_uploaded_image_paths(None)


IBB_PAGE = '<html><head><meta content="https://i.ibb.co/abc/pic.png" property="og:image" /></head></html>'


@pytest.mark.asyncio
async def test_ibb_page_link_resolves_to_direct_image_via_og_image():
    def handler(req):
        if req.url.host == "ibb.co":
            return httpx.Response(200, content=IBB_PAGE.encode())
        assert req.url.host == "i.ibb.co"
        return httpx.Response(200, content=PNG)

    async with make_client(handler) as http:
        data, filename, _ = await download_image("https://ibb.co/p6zbQKV5", http)
    assert data == PNG and filename == "image.png"


@pytest.mark.asyncio
async def test_ibb_page_og_image_pointing_to_other_domain_is_rejected():
    page = '<meta property="og:image" content="https://evil.test/x.png">'
    async with make_client(lambda req: httpx.Response(200, content=page.encode())) as http:
        with pytest.raises(ImageError, match="ImgBB"):
            await download_image("https://ibb.co/abc", http)


@pytest.mark.asyncio
async def test_ibb_page_without_og_image_is_rejected():
    async with make_client(lambda req: httpx.Response(200, content=b"<html></html>")) as http:
        with pytest.raises(ImageError, match="ImgBB"):
            await download_image("https://ibb.co/abc", http)


@pytest.mark.asyncio
async def test_malformed_url_becomes_image_error_not_generic_exception():
    """httpx.InvalidURL không thuộc HTTPError — không được lọt ra thành lỗi chung."""
    async with make_client(lambda req: httpx.Response(200, content=PNG)) as http:
        with pytest.raises(ImageError):
            await download_image("http://exa%mple.com:abc/x", http)
