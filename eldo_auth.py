"""Đăng nhập Eldorado qua AWS Cognito SRP — port gần như nguyên vẹn từ
authen.py (bản gốc), chỉ đổi threading.Lock -> asyncio.Lock vì giờ chạy
asyncio thay vì ThreadPoolExecutor. Đây là quy trình đăng nhập chính thức
(AWS Cognito SRP), không phải kỹ thuật né bot-detection nào."""
from __future__ import annotations

import asyncio
import logging
import time

import boto3
from pycognito import AWSSRP

import config

logger = logging.getLogger(__name__)

# Cognito IdToken có hạn ~1h — refresh sớm ở mốc 50 phút giống bản gốc, để
# không bao giờ vô tình dùng token đã hết hạn giữa 1 request.
REFRESH_INTERVAL_SECONDS = 50 * 60


class EldoradoAuth:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_auth_time: float | None = None
        self._cookie: str | None = None

    def _authenticate_sync(self) -> str:
        """Gọi thư viện pycognito/boto3 (đồng bộ, không có bản async chính
        thức) — chạy trong thread pool executor mặc định của asyncio để
        không chặn event loop trong lúc chờ 3 round-trip SRP."""
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
                id_token = await asyncio.to_thread(self._authenticate_sync)
                self._cookie = f"__Host-EldoradoIdToken={id_token}"
                self._last_auth_time = now
                logger.info("[auth] Đăng nhập thành công, cookie đã cập nhật.")
            return self._cookie
