"""配置管理模块（Phase A 统一入口）。

原则：
- 单一事实源：backend/.env + 进程环境变量。不再自动加载其它项目（如 HelloAgents/.env）。
- LLM 配置统一为 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT_SECONDS。
- 旧变量名（LLM_MODEL_ID / LLM_TIMEOUT / OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL）
  仅做兼容映射：命中即 Deprecation 告警；新旧同 key 且值不一致时报错，不静默。
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（config.py 位于 backend/app/config.py）
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"
load_dotenv(ENV_FILE, override=False)

# 旧变量名 -> 新变量名（兼容期专用）
_DEPRECATED_ENV_MAP = {
    "LLM_MODEL_ID": "LLM_MODEL",
    "LLM_TIMEOUT": "LLM_TIMEOUT_SECONDS",
    "OPENAI_API_KEY": "LLM_API_KEY",
    "OPENAI_BASE_URL": "LLM_BASE_URL",
    "OPENAI_MODEL": "LLM_MODEL",
}


def apply_deprecated_env_mappings(environ=None, warn=warnings.warn):
    """把旧配置名映射到新名（就地 setdefault 到 os.environ）。

    新旧同 key 且值不一致时抛 RuntimeError，避免静默使用错误配置。
    返回命中映射的旧变量名列表（供测试断言）。
    """
    env = os.environ if environ is None else environ
    applied = []
    for old, new in _DEPRECATED_ENV_MAP.items():
        value = env.get(old)
        if not value:
            continue
        if new in env and env[new] != value:
            raise RuntimeError(
                f"配置冲突：{old} 与 {new} 的值不一致，请只保留新变量 {new}"
            )
        env.setdefault(new, value)
        applied.append(old)
        warn(f"环境变量 {old} 已弃用，请改用 {new}", DeprecationWarning, stacklevel=2)
    return applied


# 必须先做映射再实例化 Settings（pydantic-settings 在构造时读环境变量）
apply_deprecated_env_mappings()


class Settings(BaseSettings):
    """应用配置（Phase A 统一版）。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        case_sensitive=False,
        extra="ignore",
    )

    # 应用基本配置
    app_env: str = "development"  # production 禁止回退到 SQLite
    app_name: str = "Dynamic Travel Agent"
    app_version: str = "1.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS 配置（字符串，逗号分隔）
    cors_origins: str = (
        "http://localhost:5173,http://localhost:3000,"
        "http://127.0.0.1:5173,http://127.0.0.1:3000"
    )

    # LLM 配置（唯一事实源，来自 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT_SECONDS）
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 30.0

    # 高德地图 API 配置（后端子进程专用；VITE_AMAP_WEB_JS_KEY 是浏览器 key，不在此读取）
    amap_api_key: str = ""
    # AMAP_TOOL_MODE: no_transit（默认，POI/步行/驾车）| full（显式启用公交）| places_only
    # 公交 MCP 调用常受限流和超时影响；行程只以步行/驾车距离控制空间跨度。
    amap_tool_mode: str = "no_transit"
    # 多 Agent 共用高德 MCP 时，连接池允许慢请求重叠等待；总 QPS 仍全局受控。
    amap_mcp_pool_size: int = 2
    amap_max_qps: float = 1.5
    # 百度图片搜索策略（opt-in，无需 key，但百度反爬协议可能变化，高频调用会被限速）
    baidu_image_enabled: bool = False
    baidu_image_endpoint: str = (
        "https://image.baidu.com/search/acjson"
        "?tn=resultjson&ie=utf-8&fp=detail&rn=1&fmt=json&word={word}"
    )

    # Unsplash（可选图片服务）
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # 迁移期开关：true 时保留旧的固定四 Agent /api/trip/plan 路径（已弃用标记）
    enable_legacy_planner: bool = False

    # MySQL production URL.  SQLite remains an explicit legacy/test adapter
    # when this value is absent, so deterministic unit tests need no database.
    database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_seconds: float = 30.0
    db_pool_recycle_seconds: int = 1800

    # Legacy/test SQLite path (relative paths are resolved from backend/).
    db_path: str = "data/travel_runs.sqlite"

    # 身份认证默认启用；本地临时匿名调试必须显式设置 AUTH_REQUIRED=false。
    # 任何部署环境都应配置随机 AUTH_SECRET_KEY。
    auth_required: bool = True
    auth_secret_key: SecretStr = SecretStr("change-this-development-secret-before-production")
    access_token_ttl_minutes: int = 60 * 24 * 7

    # Shared-key virtual credits. Integer smallest unit; no provider balance is queried.
    default_new_user_points: int = 10000
    quota_reserve_prompt_tokens: int = 4000
    quota_reserve_completion_tokens: int = 1500

    # Optional acceleration only; MySQL remains authoritative.
    redis_url: str | None = None
    redis_quota_cache_ttl_seconds: int = 15
    api_rate_limit_requests: int = 120
    api_rate_limit_window_seconds: int = 60
    llm_rate_limit_requests: int = 30
    tool_rate_limit_requests: int = 90
    redis_idempotency_ttl_seconds: int = 300
    redis_agent_context_ttl_seconds: int = 120

    # Process-local graph worker slots.  This bounds local CPU/socket pressure;
    # task fan-out is separately decided by the Complexity Router.
    agent_worker_pool_size: int = 4
    agent_worker_queue_size: int = 64

    # Optional AES-256-GCM master key for BYOK credentials (base64-encoded 32 bytes).
    credential_encryption_key: SecretStr | None = None

    # 日志配置
    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(
            self.llm_api_key and self.llm_api_key.get_secret_value()
            and self.llm_base_url and self.llm_model
        )

    def resolve_db_path(self) -> Path:
        path = Path(self.db_path).expanduser()
        return path if path.is_absolute() else (BACKEND_DIR / path)


# 全局配置实例（兼容既有 get_settings() 调用方）
settings = Settings()


def get_settings() -> Settings:
    """获取全局配置实例。"""
    return settings


def validate_core_config(cfg: Settings | None = None) -> List[str]:
    """返回新 Agent 链路必需配置的缺失项列表（不抛错）。

    生产装配（bootstrap.build_travel_app）会在缺失时 raise；此函数供 API/测试复用。
    """
    cfg = cfg or settings
    errors: List[str] = []
    if not (cfg.llm_api_key and cfg.llm_api_key.get_secret_value()):
        errors.append("LLM_API_KEY 未配置")
    if not cfg.llm_base_url:
        errors.append("LLM_BASE_URL 未配置")
    if not cfg.llm_model:
        errors.append("LLM_MODEL 未配置")
    if cfg.app_env.lower() == "production" and not cfg.database_url:
        errors.append("APP_ENV=production 时必须配置 DATABASE_URL")
    if cfg.auth_required:
        secret = cfg.auth_secret_key.get_secret_value()
        if secret == "change-this-development-secret-before-production" or secret.startswith("replace-with-"):
            errors.append("AUTH_REQUIRED=true 时必须设置随机 AUTH_SECRET_KEY")
    if cfg.app_env.lower() == "production" and not cfg.credential_encryption_key:
        errors.append("APP_ENV=production 时必须配置 CREDENTIAL_ENCRYPTION_KEY")
    return errors
