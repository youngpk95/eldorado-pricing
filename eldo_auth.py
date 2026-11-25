"""Đăng nhập Eldorado qua AWS Cognito SRP — port gần như nguyên vẹn từ
authen.py (bản gốc), chỉ đổi threading.Lock -> asyncio.Lock vì giờ chạy
asyncio thay vì ThreadPoolExecutor. Đây là quy trình đăng nhập chính thức
(AWS Cognito SRP), không phải kỹ thuật né bot-detection nào.

Từ 2026-11-16 có thêm SellerApiAuth — đăng nhập bằng Seller API CHÍNH THỨC
(client credentials do nick owner tạo ở Dashboard > Seller API). Cả 2 lớp có
chung giao diện `get_auth_headers()` / `invalidate()`; `build_auth()` chọn
lớp theo .env (có credentials -> Seller API, không có -> Cognito như cũ)."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

import config
from eldorado_api import EldoradoApiError

logger = logging.getLogger(__name__)

# Cognito IdToken có hạn ~1h — refresh sớm ở mốc 50 phút giống bản gốc, để
# không bao giờ vô tình dùng token đã hết hạn giữa 1 request.
REFRESH_INTERVAL_SECONDS = 50 * 60
# Quá mốc này coi như token đã hết hạn thật -> không dùng tiếp cookie cũ khi login lỗi.
TOKEN_HARD_EXPIRY_SECONDS = 58 * 60
# Login lỗi nhưng cookie cũ còn hạn: thử đăng nhập lại sau ~60 giây.
LOGIN_FAILURE_RETRY_SECONDS = 60
# Chờ giữa các lần thử lại trong 1 lần đăng nhập (giây).
LOGIN_RETRY_DELAYS = (15, 45)


class EldoradoAuth:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_auth_time: float | None = None
        self._cookie: str | None = None

    def _authenticate_sync(self) -> str:
        """Gọi thư viện pycognito/boto3 (đồng bộ, không có bản async chính
        thức) — chạy trong thread pool executor mặc định của asyncio để
        không chặn event loop trong lúc chờ 3 round-trip SRP."""
        # Import trong hàm: máy dùng Seller API không cần boto3/pycognito.
        import boto3
        from pycognito import AWSSRP

        cognito = boto3.client("cognito-idp", region_name=config.ELDORADO_COGNITO_REGION)
        aws_srp = AWSSRP(
            username=config.ELDORADO_EMAIL,
            password=config.ELDORADO_PASSWORD,
            pool_id=config.ELDORADO_COGNITO_POOL_ID,
            client_id=config.ELDORADO_COGNITO_CLIENT_ID,
            client=cognito,
        )
        auth_params = aws_srp.get_auth_params()
        response = cognito.initiate_auth(
            AuthFlow="USER_SRP_AUTH",
            AuthParameters=auth_params,
            ClientId=config.ELDORADO_COGNITO_CLIENT_ID,
        )
        if response["ChallengeName"] != "PASSWORD_VERIFIER":
            raise RuntimeError(f"Cognito trả về challenge không mong đợi: {response['ChallengeName']}")
        challenge_response = aws_srp.process_challenge(response["ChallengeParameters"], auth_params)
        response = cognito.respond_to_auth_challenge(
            ClientId=config.ELDORADO_COGNITO_CLIENT_ID,
            ChallengeName="PASSWORD_VERIFIER",
            ChallengeResponses=challenge_response,
        )
        return response["AuthenticationResult"]["IdToken"]

    async def get_cookie(self) -> str:
        """Trả về cookie hiện tại, tự refresh nếu đã quá hạn — 1 lock chung
        đảm bảo nhiều task song song không cùng login trùng lúc."""
        async with self._lock:
            now = time.time()
            if self._cookie is None or self._last_auth_time is None or (now - self._last_auth_time) >= REFRESH_INTERVAL_SECONDS:
                logger.info("[auth] Đăng nhập Eldorado (Cognito SRP)...")
                try:
                    id_token = await self._login_with_retry()
                except Exception as e:
                    # Log 2026-11-22/23: Cognito TooManyRequestsException làm cả chu kỳ lỗi.
                    # Cookie cũ còn hạn (token ~1h, ta refresh ở 50 phút) -> dùng tiếp, thử lại sau.
                    if self._cookie is not None and self._last_auth_time is not None and (
                        now - self._last_auth_time
                    ) < TOKEN_HARD_EXPIRY_SECONDS:
                        logger.warning(
                            "[auth] Đăng nhập lại lỗi (%s) — dùng tiếp cookie cũ (còn hạn), thử lại ở lần sau.",
                            type(e).__name__,
                        )
                        self._last_auth_time = now - REFRESH_INTERVAL_SECONDS + LOGIN_FAILURE_RETRY_SECONDS
                        return self._cookie
                    raise
                self._cookie = f"__Host-EldoradoIdToken={id_token}"
                self._last_auth_time = now
                logger.info("[auth] Đăng nhập thành công, cookie đã cập nhật.")
            return self._cookie

    async def _login_with_retry(self) -> str:
        """Đăng nhập; gặp lỗi (vd Cognito 'Too many requests') thì chờ rồi thử
        lại tối đa LOGIN_RETRY_DELAYS — không retry dồn dập (tránh bị chặn lâu hơn)."""
        last: Exception | None = None
        for delay in (0, *LOGIN_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await asyncio.to_thread(self._authenticate_sync)
            except Exception as e:  # noqa: BLE001 — mọi lỗi đăng nhập đều thử lại có giới hạn
                last = e
                logger.warning("[auth] Đăng nhập lỗi: %s", type(e).__name__)
        assert last is not None
        raise last

    async def get_auth_headers(self) -> dict[str, str]:
        return {"Cookie": await self.get_cookie()}

    def invalidate(self, stale_headers: dict[str, str] | None = None) -> None:
        """Eldorado trả 401 -> lần sau bắt buộc đăng nhập lại. Bỏ qua nếu
        cookie đã được làm mới sau khi request đó gửi đi (dòng khác vừa login)."""
        if stale_headers is not None and stale_headers.get("Cookie") != self._cookie:
            return
        self._cookie = None


SELLER_TOKEN_URL = "https://www.eldorado.gg/api/authentication/seller/token"
# Token Seller API sống 900s (15 phút) — xin token mới sớm hơn 2 phút.
SELLER_TOKEN_REFRESH_MARGIN_SECONDS = 120
# Lấy token lỗi -> trong khoảng này báo lỗi luôn, không POST lại cho từng dòng.
SELLER_TOKEN_FAILURE_RETRY_SECONDS = 60


class SellerApiAuth:
    """Đăng nhập Seller API chính thức: đổi Client ID + Secret lấy access
    token (POST /api/authentication/seller/token), gửi kèm header
    `Authorization: Bearer <token>`. Đã test thật 2026-11-16: token đọc được
    offer/chi tiết/so sánh và gọi được lệnh ghi (changePrice) như cookie cũ."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._refresh_at = 0.0
        self._failed_until = 0.0
        self._last_error = ""

    async def _fetch_token(self) -> tuple[str, float]:
        async with httpx.AsyncClient(timeout=config.ELDORADO_TIMEOUT_SECONDS) as http:
            resp = await http.post(
                SELLER_TOKEN_URL,
                json={"clientId": self._client_id, "clientSecret": self._client_secret},
                headers={"User-Agent": self._user_agent, "Accept": "application/json"},
            )
        if resp.status_code != 200:
            # KHÔNG log body/secret — chỉ status.
            raise EldoradoApiError(
                f"Lấy token Seller API thất bại (status {resp.status_code}) — kiểm tra "
                "ELDORADO_SELLER_CLIENT_ID/SECRET trong .env (có thể đã hết hạn hoặc bị thu hồi)"
            )
        data = resp.json()
        token = data.get("accessToken")
        if not token:
            raise EldoradoApiError("Response token Seller API không có accessToken — Eldorado có thể đã đổi API")
        expires_in = float(data.get("expiresIn") or 900)
        return token, expires_in

    async def get_auth_headers(self) -> dict[str, str]:
        async with self._lock:
            now = time.time()
            if self._token is None or now >= self._refresh_at:
                if now < self._failed_until:
                    if self._token is not None and now < self._expires_at:
                        return {"Authorization": f"Bearer {self._token}"}  # token cũ còn hạn
                    raise EldoradoApiError(self._last_error)
                logger.info("[auth] Lấy token Seller API...")
                try:
                    token, expires_in = await self._fetch_token()
                except Exception as e:
                    self._failed_until = now + SELLER_TOKEN_FAILURE_RETRY_SECONDS
                    self._last_error = str(e) if isinstance(e, EldoradoApiError) else f"Lỗi lấy token Seller API: {e}"
                    logger.error("[auth] %s", self._last_error)
                    if self._token is not None and now < self._expires_at:
                        # Làm mới sớm lỗi nhưng token cũ còn hạn -> vẫn dùng tiếp, đừng từ chối request.
                        logger.warning("[auth] Dùng tiếp token cũ (còn hạn) trong khi chờ thử lại.")
                        return {"Authorization": f"Bearer {self._token}"}
                    raise EldoradoApiError(self._last_error) from e
                self._token = token
                self._expires_at = now + expires_in
                # Token sống quá ngắn -> làm mới ở nửa thời hạn, tránh xin
                # token mới trước MỌI request.
                self._refresh_at = self._expires_at - min(SELLER_TOKEN_REFRESH_MARGIN_SECONDS, expires_in / 2)
                logger.info("[auth] Có token Seller API mới (hạn %ds).", int(expires_in))
            return {"Authorization": f"Bearer {self._token}"}

    def invalidate(self, stale_headers: dict[str, str] | None = None) -> None:
        """Eldorado trả 401 -> lần sau xin token mới. Bỏ qua nếu token đã được
        làm mới sau khi request đó gửi đi (dòng khác vừa xin)."""
        if stale_headers is not None and stale_headers.get("Authorization") != f"Bearer {self._token}":
            return
        self._token = None
        self._refresh_at = 0.0


def build_auth(user_agent: str):
    """Chọn cách đăng nhập theo .env — có Seller API credentials thì dùng."""
    if config.USE_SELLER_API:
        logger.info("[auth] Dùng Seller API chính thức (client credentials).")
        return SellerApiAuth(config.ELDORADO_SELLER_CLIENT_ID.strip(), config.ELDORADO_SELLER_CLIENT_SECRET.strip(), user_agent)
    logger.warning(
        "[auth] Đang dùng đăng nhập Cognito SRP (phương thức CŨ) — Eldorado thông báo sẽ tắt "
        "(26/11/2026 UTC). Hãy thêm ELDORADO_SELLER_CLIENT_ID/SECRET vào .env (owner tạo)."
    )
    return EldoradoAuth()
