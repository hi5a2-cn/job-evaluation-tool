from collections.abc import Callable
import hashlib
import secrets
import time
import sqlite3
from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from jet.db.store import get_conn, utc_now
from jet.logging import get_logger

_log = get_logger("jet.auth")


class PairingCodes:
    """In-memory one-time pairing code manager."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or time.time
        self.code: str | None = None
        self.expires_at: float = 0.0
        self.attempts: int = 0

    def issue(self) -> str:
        """Issue a 6-digit numeric pairing code valid for 5 minutes (300 seconds)."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.code = code
        self.expires_at = self.clock() + 300.0
        self.attempts = 0
        return code

    def verify(self, code: str) -> str:
        """Verify the pairing code, returning 'ok', 'bad_code', or 'too_many_attempts'."""
        now = self.clock()
        if self.code is None or now > self.expires_at:
            return "bad_code"

        # Already failed 5 times: 6th attempt onwards returns too_many_attempts
        if self.attempts >= 5:
            return "too_many_attempts"

        if code == self.code:
            self.code = None
            self.expires_at = 0.0
            self.attempts = 0
            return "ok"

        self.attempts += 1
        return "bad_code"


class OriginProtectionMiddleware(BaseHTTPMiddleware):
    """
    Middleware ensuring requests with an Origin header originate from a chrome-extension.

    NOTE on CORS:
    We do NOT add CORSMiddleware and do NOT return Access-Control-Allow-* headers.
    The extension declares host_permissions for 'http://127.0.0.1/*', which exempts
    its background service worker fetch calls from CORS restrictions. This simplifies
    the implementation while strictly blocking arbitrary web origins.
    """

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and not origin.startswith("chrome-extension://"):
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden_origin", "message": "禁止的来源"},
            )
        return await call_next(request)


def _reject(reason: str, request: Request) -> None:
    # 打印拒绝原因（不含令牌），便于在 jet serve 终端里排查"未配对"
    _log.warning(
        "未配对请求 %s %s：%s（Origin=%s）",
        request.method,
        request.url.path,
        reason,
        request.headers.get("origin") or "无",
    )
    raise HTTPException(
        status_code=401,
        detail={"error": "unpaired", "message": "未配对"},
    )


def require_paired(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> str:
    """Dependency verifying the Bearer token of a paired extension.

    The token is the credential. Chrome does not send an Origin header on every
    extension service-worker request (observed: GET requests arrived without one while
    POST /v1/pair carried it), so a missing Origin is accepted. When Origin IS present it
    must equal the origin recorded at pairing; non-extension origins are already rejected
    with 403 by OriginProtectionMiddleware.
    """
    auth_header = request.headers.get("authorization")
    origin = request.headers.get("origin")

    if not auth_header or not auth_header.startswith("Bearer "):
        _reject("缺少令牌", request)

    token = auth_header.split(" ", 1)[1].strip()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    row = conn.execute(
        "SELECT id, user_id, extension_origin, revoked_at FROM pairings WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()

    if not row:
        _reject("令牌不存在", request)
    if row["revoked_at"] is not None:
        _reject("令牌已撤销", request)
    if origin is not None and row["extension_origin"] != origin:
        _reject(f"Origin 与配对时不一致（配对时为 {row['extension_origin']}）", request)

    # Update last_used_at
    conn.execute(
        "UPDATE pairings SET last_used_at = ? WHERE id = ?",
        (utc_now(), row["id"]),
    )

    return str(row["user_id"])
