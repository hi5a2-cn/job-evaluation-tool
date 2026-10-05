from collections.abc import Callable
from contextlib import asynccontextmanager
import time
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx

from jet.api.auth import OriginProtectionMiddleware, PairingCodes
from jet.api.routes import router
from jet.config import Settings
from jet.db.store import (
    connect,
    count_ownerless_rows,
    init_db,
    reset_pending_reviews_to_failed,
    reset_running_to_interrupted,
)
from jet.logging import get_logger
from jet.worker import JudgementWorker

_http_log = get_logger("jet.http")


def create_app(
    settings: Settings,
    *,
    admin_secret: str | None = None,
    clock: Callable[[], float] | None = None,
    llm_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    worker = JudgementWorker(settings, transport=llm_transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 1. Startup: init DB, reset running, self-check ownerless rows
        init_db(settings.data_dir)
        conn = connect(settings.data_dir)
        reset_running_to_interrupted(conn)
        reset_pending_reviews_to_failed(conn)
        # 配置（数据目录 .env 的 JET_DAILY_LLM_LIMIT 与 JET_DAILY_ASSIST_LIMIT）是每日额度的唯一来源：每次启动写入数据库
        conn.execute(
            "UPDATE user_settings SET daily_llm_limit = ?, daily_assist_limit = ? WHERE user_id = 'me'",
            (settings.daily_llm_limit, settings.daily_assist_limit),
        )
        ownerless = count_ownerless_rows(conn)
        if ownerless > 0:
            conn.close()
            raise RuntimeError(f"Ownerless rows detected: {ownerless}. Refusing to start.")

        app.state.conn = conn

        # Start background worker
        worker.start()
        app.state.worker = worker

        # 上次运行时已排队但还没开始的判断：队列只在内存里，重启后重新交给 worker，
        # 否则插件会一直显示"判断中"
        for row in conn.execute("SELECT id FROM judgements WHERE status = 'queued' ORDER BY id").fetchall():
            worker.submit(row["id"])

        try:
            yield
        finally:
            # 2. Shutdown: stop worker, close DB connection, clean up admin.secret
            worker.stop()
            conn.close()
            if getattr(app.state, "created_admin_secret_file", False):
                secret_file = settings.data_dir / "admin.secret"
                secret_file.unlink(missing_ok=True)

    app = FastAPI(title="Jet", lifespan=lifespan)

    # State
    app.state.settings = settings
    app.state.admin_secret = admin_secret
    app.state.pairing_codes = PairingCodes(clock=clock)
    app.state.worker = worker
    app.state.llm_transport = llm_transport

    # Middleware
    app.add_middleware(OriginProtectionMiddleware)

    @app.middleware("http")
    async def request_duration_middleware(request: Request, call_next: Callable):
        if not (request.url.path == "/v1" or request.url.path.startswith("/v1/")):
            return await call_next(request)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = max(0, int(round((time.perf_counter() - start) * 1000)))
            _http_log.info(f"{request.method} {request.url.path} 500 {duration_ms}ms")
            raise
        duration_ms = max(0, int(round((time.perf_counter() - start) * 1000)))
        response.headers["X-Jet-Duration-Ms"] = str(duration_ms)
        _http_log.info(f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms")
        return response

    # Error Handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_payload", "message": "请求体格式错误"},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "error", "message": str(exc.detail)},
        )

    app.include_router(router)
    return app
