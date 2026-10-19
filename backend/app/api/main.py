"""FastAPI 主应用（Phase A：create_app 工厂 + lifespan 装配）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from ..bootstrap import BootstrapError, build_travel_app
from ..config import Settings, get_settings
from ..identity import AuthService
from ..errors import AppError, AuthenticationError, ResourceNotFoundError, sanitize_exception
from ..trace_context import get_trace_id, new_trace_id, reset_trace_id, set_trace_id
from .routes import auth
from .routes import map as map_routes
from .routes import poi, quota, runs, trip, credentials


def create_app(settings: Settings | None = None, processor=None) -> FastAPI:
    """创建 FastAPI 应用（唯一生产入口）。

    - lifespan 启动时执行 build_travel_app（唯一生产装配根）；配置缺失抛 BootstrapError，
      启动显式失败，不做任何 Fake 自动降级。
    - processor：默认 None → 装配真实 M1→M5 链路；测试可显式注入替身。
    - 挂载：/api/trip（兼容）、/api/travel/runs（新链路）、/api/poi、/api/map、/health。
    """
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            travel = build_travel_app(cfg, processor=processor)
            await travel.start()  # 启动可选组件（AMap MCP）；失败显式报错
        except BootstrapError as error:
            # 缺必要配置 → 启动失败（明确、可诊断），而不是静默用假数据
            raise RuntimeError(str(error)) from error
        app.state.travel_app = travel
        app.state.settings = cfg
        app.state.geo = travel.geo
        app.state.user_repository = travel.user_repository
        from ..redis_store import CachedQuotaRepository, InMemoryRedis, RedisStore
        try:
            cache = await RedisStore.connect(cfg.redis_url) if cfg.redis_url else InMemoryRedis()
        except Exception:
            # Redis is acceleration only; MySQL correctness must remain available.
            cache = InMemoryRedis()
        app.state.redis_store = cache
        travel.context_cache = cache
        travel.context_cache_ttl = cfg.redis_agent_context_ttl_seconds
        llm = getattr(getattr(travel, "processor", None), "llm", None)
        if llm is not None:
            llm.rate_limiter = cache
            llm.rate_limit = cfg.llm_rate_limit_requests
        tools = getattr(travel, "tool_registry", None)
        if tools is not None:
            tools.rate_limiter = cache
            tools.rate_limit = cfg.tool_rate_limit_requests
        if getattr(travel, "quota_repository", None) is not None:
            app.state.quota_repository = CachedQuotaRepository(travel.quota_repository, cache, cfg.redis_quota_cache_ttl_seconds)
        yield
        await cache.close()
        await travel.close()

    app = FastAPI(
        title=cfg.app_name,
        version=cfg.app_version,
        description="Dynamic Travel Agent API（Phase A 装配边界）",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def trace_context(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
        token = set_trace_id(trace_id)
        try:
            cache = getattr(request.app.state, "redis_store", None)
            if cache is not None and request.url.path.startswith("/api/"):
                raw_identity = request.headers.get("authorization") or (request.client.host if request.client else "unknown")
                # Never persist a bearer credential as a Redis key.
                identity = hashlib.sha256(raw_identity.encode()).hexdigest()[:24]
                if not await cache.allow(f"ratelimit:{identity}", cfg.api_rate_limit_requests, cfg.api_rate_limit_window_seconds):
                    return JSONResponse(status_code=429, content={"code":"RATE_LIMITED","message":"请求过于频繁，请稍后重试。","trace_id":trace_id})
            # Only state-changing travel requests participate in idempotency.
            idem = request.headers.get("Idempotency-Key")
            eligible = request.method == "POST" and (request.url.path == "/api/travel/runs" or request.url.path.endswith("/messages"))
            idem_key = None
            if cache is not None and idem and eligible:
                subject = hashlib.sha256((raw_identity + ":" + idem + ":" + request.url.path).encode()).hexdigest()
                idem_key = f"idempotency:{subject}"
                existing = await cache.get_text(idem_key)
                if existing and existing != "LOCK":
                    # Preserve the original status (notably POST /runs=201)
                    # as well as the body.  A replay must be observationally
                    # equivalent to the original successful request.
                    try:
                        replay = json.loads(existing)
                        return Response(content=replay["body"], status_code=replay["status"], media_type="application/json", headers={"X-Trace-Id":trace_id, "Idempotency-Replayed":"true"})
                    except (ValueError, KeyError, TypeError):
                        # Old cache entries from the pre-envelope format may
                        # exist during a rolling deployment; safely treat them
                        # as a miss instead of replaying an ambiguous result.
                        await cache.invalidate(idem_key)
                if existing == "LOCK" or not await cache.claim(idem_key, cfg.redis_idempotency_ttl_seconds):
                    return JSONResponse(status_code=409, content={"code":"REQUEST_IN_PROGRESS","message":"相同请求正在处理中。","trace_id":trace_id})
            response = await call_next(request)
            if cache is not None and idem_key:
                body = b"".join([chunk async for chunk in response.body_iterator])
                response = Response(content=body, status_code=response.status_code, media_type=response.media_type, headers=dict(response.headers))
                if response.status_code < 400:
                    await cache.set_text(
                        idem_key,
                        json.dumps({"status": response.status_code, "body": body.decode("utf-8")}),
                        cfg.redis_idempotency_ttl_seconds,
                    )
                else:
                    await cache.invalidate(idem_key)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            reset_trace_id(token)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError):
        trace_id = get_trace_id() or new_trace_id()
        cache = getattr(request.app.state, "redis_store", None)
        if cache is not None:
            await cache.increment(f"errors:{error.code}", 300)
        # Cause/context are intentionally not serialized. Sanitization makes
        # this safe for the basic local logger without recording prompts/keys.
        import logging
        logging.getLogger("trip_planner.error").warning("app_error code=%s trace_id=%s context=%s cause=%s", error.code, trace_id, error.internal_context, sanitize_exception(error.cause or ""))
        return JSONResponse(status_code=error.http_status, content={"code": error.code, "message": error.public_message, "trace_id": trace_id})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "请求参数不符合要求。", "trace_id": get_trace_id() or new_trace_id()})

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException):
        trace_id = get_trace_id() or new_trace_id()
        if error.status_code == 401:
            body = {"code": "AUTH_INVALID", "message": "登录状态无效，请重新登录。", "trace_id": trace_id}
        elif error.status_code == 404:
            body = {"code": "RESOURCE_NOT_FOUND", "message": "请求的资源不存在。", "trace_id": trace_id}
        else:
            body = {"code": "REQUEST_FAILED", "message": "请求暂时无法完成，请稍后重试。", "trace_id": trace_id}
        return JSONResponse(status_code=error.status_code, content=body, headers=error.headers)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception):
        trace_id = get_trace_id() or new_trace_id()
        cache = getattr(request.app.state, "redis_store", None)
        if cache is not None:
            await cache.increment("errors:INTERNAL_ERROR", 300)
        import logging
        logging.getLogger("trip_planner.error").exception("unexpected_error trace_id=%s error=%s", trace_id, sanitize_exception(error))
        return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "服务暂时不可用，请稍后重试。", "trace_id": trace_id})

    app.include_router(trip.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(auth.memory_router, prefix="/api")
    app.include_router(poi.router, prefix="/api")
    app.include_router(map_routes.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(quota.router, prefix="/api")
    app.include_router(credentials.router, prefix="/api")
    app.state.auth_service = AuthService(
        cfg.auth_secret_key.get_secret_value(), cfg.access_token_ttl_minutes
    )

    @app.get("/")
    async def root():
        return {
            "name": cfg.app_name,
            "version": cfg.app_version,
            "status": "running",
            "docs": "/docs",
            "redoc": "/redoc",
        }

    @app.get("/health")
    async def health():
        travel = getattr(app.state, "travel_app", None)
        return {
            "status": "healthy",
            "service": cfg.app_name,
            "version": cfg.app_version,
            "travel_engine": travel is not None,
            "llm_configured": cfg.llm_configured,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=get_settings().host,
        port=get_settings().port,
        # Windows 的 --reload 强制 SelectorEventLoop，不能承载 MCP stdio 子进程。
        reload=os.name != "nt",
    )
