"""唯一生产装配根（bootstrap）。

Phase C 装配：
- 真实 M1→M5（DeepSeek adapter / IntentService / RoutingService / PlanningService /
  GraphRuntime(ReAct) / SQLite checkpoint / weather 只读工具）
- AMap MCP（可选）：settings.amap_api_key 存在时，service.start() 内异步连接
  uvx amap-mcp-server → 白名单注册只读工具 → 扩展执行器能力与 GeoFacade；
  连接失败显式报错（不静默降级、不自动 fallback 到 fake/旧 AmapService）。

测试通过参数显式注入替身；本模块不得 import tests/fakes。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Set

from .application import (
    IntentService,
    PlanningService,
    RoutingService,
    TravelApplicationService,
    TravelPipelineProcessor,
)
from .application.travel_service import MessageProcessor
from .application.replanning_service import ReplanningService
from .config import Settings, validate_core_config
from .domain.budget import DEFAULT_EXECUTION_BUDGET
from .evaluation import DeterministicTripEvaluator
from .executors import BudgetPolicyExecutor, EvidenceHandoffExecutor
from .executors.react import AgentProfile, ReActExecutor
from .intent.llm_parser import LLMIntentParser
from .persistence import SQLiteRunRepository
from .persistence.user_repository import SQLiteUserRepository
from .persistence.mysql_repository import MySQLRunRepository, MySQLUserRepository
from .persistence.database import create_session_factory, dispose_session_factory
from .identity import MemoryService
from .planning.builder import PlanBuilder
from .planning.llm_structured import LLMStructuredPlanner
from .providers import OpenAICompatibleToolCallingLLM
from .telemetry import MySQLLLMTelemetryRepository, TelemetryLLMGateway
from .quota import MySQLQuotaRepository, PricingRegistry, QuotaService
from .runtime.graph_runtime import GraphRuntime
from .runtime.registry import ExecutorRegistry
from .runtime.worker_pool import GlobalWorkerPool
from .runtime.state_store import SQLiteStateStore
from .runtime.mysql_state_store import MySQLStateStore
from .routing.llm_analyzer import LLMComplexityAnalyzer
from .routing.policy import ComplexityRouter
from .services.attraction_image import AttractionImageResolver
from .services.baidu_image_service import BaiduImageService
from .synthesis import TripPlanSynthesizer
from .tools import OpenMeteoWeatherTool, ToolRegistry

log = logging.getLogger(__name__)


class BootstrapError(RuntimeError):
    """生产装配失败（配置缺失 / 组件不可用）。"""


# Phase C 真实能力基线（AMap 启动后追加 place_research/geospatial_reasoning 等）
DEFAULT_CAPABILITIES: Set[str] = {"weather_research", "budget_policy", "itinerary_synthesis"}


@dataclass
class PipelineParts:
    processor: TravelPipelineProcessor
    llm: Any
    tools: ToolRegistry
    executor_registry: ExecutorRegistry
    runtime: GraphRuntime
    worker_pool: GlobalWorkerPool
    capabilities: Set[str]


def build_travel_app(
    settings: Settings,
    *,
    processor: MessageProcessor | None = None,
    repository: SQLiteRunRepository | None = None,
    capabilities: Set[str] | None = None,
    llm=None,
    enable_amap: Optional[bool] = None,
) -> TravelApplicationService:
    """构造生产 TravelApplicationService（真实 M1→M5 + 可选 AMap MCP）。"""
    missing = validate_core_config(settings)
    if missing:
        raise BootstrapError(
            "生产配置缺失，新 Agent 链路不可用："
            + "；".join(missing)
            + "。请检查 backend/.env（不要自动回退到旧 Agent 或 Fake）。"
        )

    sessions = create_session_factory(settings) if settings.database_url else None
    repo = repository or (MySQLRunRepository(sessions) if sessions else SQLiteRunRepository(settings.resolve_db_path()))
    user_repository = MySQLUserRepository(sessions, default_new_user_points=settings.default_new_user_points) if sessions else SQLiteUserRepository(settings.resolve_db_path())
    checkpoint_store = MySQLStateStore(sessions) if sessions else SQLiteStateStore(settings.resolve_db_path())

    if processor is not None:
        app = TravelApplicationService(repo, settings=settings, processor=processor)
        app.user_repository = user_repository
        app.quota_repository = MySQLQuotaRepository(sessions, default_new_user_points=settings.default_new_user_points) if sessions else None
        if sessions is not None and settings.credential_encryption_key:
            from .credentials import CredentialCipher, CredentialRepository
            app.credential_repository = CredentialRepository(sessions, CredentialCipher(settings.credential_encryption_key.get_secret_value()))
            # Test-injected processors may expose the same gateway directly.
            if hasattr(processor, "llm"): processor.llm.credential_repository = app.credential_repository
        if sessions is not None:
            app.register_lifecycle(close=lambda: dispose_session_factory(sessions))
        log.info("travel application bootstrapped (injected processor=%s)", type(processor).__name__)
        return app

    parts = _build_pipeline_parts(settings, capabilities=capabilities, llm=llm, checkpoint_store=checkpoint_store, user_repository=user_repository)
    app = TravelApplicationService(repo, settings=settings, processor=parts.processor)
    app.user_repository = user_repository
    app.quota_repository = MySQLQuotaRepository(sessions, default_new_user_points=settings.default_new_user_points) if sessions else None
    if sessions is not None and settings.credential_encryption_key:
        from .credentials import CredentialCipher, CredentialRepository
        app.credential_repository = CredentialRepository(sessions, CredentialCipher(settings.credential_encryption_key.get_secret_value()))
        parts.llm.credential_repository = app.credential_repository
    app.tool_registry = parts.tools
    # The pool is process-local and reusable across independent Runs.  It is
    # deliberately lifecycle-managed with the app, never created per request.
    app.register_lifecycle(start=parts.worker_pool.start, close=parts.worker_pool.close)
    if sessions is not None:
        app.register_lifecycle(close=lambda: dispose_session_factory(sessions))

    amap_on = bool(settings.amap_api_key) and (enable_amap is not False)
    if amap_on:
        holder: dict[str, Any] = {}

        async def _start() -> None:
            from .services.geo_facade import GeoFacade
            from .tools.mcp.pool import MCPPoolToolHandle
            from .tools.mcp.amap import AMAP_CAPABILITY_MAP, build_amap_adapter, build_amap_pool

            # AMAP_TOOL_MODE: no_transit（默认）| full（显式启用公交）| places_only（只留 POI 类）
            mode = (getattr(settings, "amap_tool_mode", None) or "no_transit").lower()
            if mode in {"places_only", "poi_only"}:
                allowed = {
                    name: meta
                    for name, meta in AMAP_CAPABILITY_MAP.items()
                    if "place_research" in (meta.get("capabilities") or [meta.get("capability")])
                }
            elif mode == "no_transit":
                allowed = {
                    name: meta
                    for name, meta in AMAP_CAPABILITY_MAP.items()
                    if "transit" not in name
                }
            else:
                allowed = dict(AMAP_CAPABILITY_MAP)

            # 每个池成员是独立 stdio MCP 连接；池在全局层面限制同一 AMap key 的 QPS。
            pool = build_amap_pool(settings, allowed=allowed, adapter_factory=build_amap_adapter)
            specs = await pool.start()
            holder["pool"] = pool
            handles = [MCPPoolToolHandle(pool, spec) for spec in specs]
            for handle in handles:
                parts.tools.register(handle)

            amap_tool_names = {handle.spec.name for handle in handles}
            registered_caps = set().union(*(handle.spec.capabilities for handle in handles))
            # AMap 执行器：地点/地理工具 + 合成能力（不混 weather 工具），减少跨域空转；
            # maps_weather 仅保留给 /api/map 直连（GeoFacade），不进 agent 执行器白名单
            executor_tool_names = amap_tool_names - {"maps_weather"}
            amap_caps = registered_caps - {"weather_research"}
            parts.executor_registry.register(
                ReActExecutor(
                    "react-amap",
                    parts.llm,
                    parts.tools,
                    AgentProfile(
                        "amap-agent", amap_caps, executor_tool_names, max_steps=6, max_tool_calls=9,
                        tool_call_limits={
                            "maps_text_search": 3,
                            "maps_search_detail": 4,
                            "maps_direction_walking_by_address": 2,
                            "maps_direction_driving_by_address": 2,
                        },
                    ),
                ),
                amap_caps,
                prepend=True,
            )
            # A task may legitimately combine weather and place capabilities.
            # Specialised executors retain priority; this fallback covers their union.
            combined_caps = parts.capabilities | amap_caps
            parts.executor_registry.register(
                ReActExecutor('react-travel', parts.llm, parts.tools,
                              AgentProfile(
                                  'travel-agent', combined_caps, executor_tool_names | {'weather'},
                                  max_steps=6, max_tool_calls=9,
                                  tool_call_limits={
                                      "maps_text_search": 3,
                                      "maps_search_detail": 4,
                                      "maps_direction_walking_by_address": 2,
                                      "maps_direction_driving_by_address": 2,
                                  },
                              )),
                combined_caps,
            )
            parts.capabilities |= amap_caps
            parts.processor.capabilities |= amap_caps
            parts.worker_pool.add_capabilities(amap_caps)
            app.geo = GeoFacade(parts.tools, allowed_tools=amap_tool_names, provider="amap")
            log.info("AMap MCP started: %d read-only tools registered", len(amap_tool_names))

        async def _close() -> None:
            pool = holder.get("pool")
            if pool is not None:
                await pool.close()

        app.register_lifecycle(start=_start, close=_close)
        # geo 在 start() 后才可用（连接成功即赋值）
        log.info("travel application bootstrapped (Phase C; amap enabled, connect on start())")
    else:
        log.info(
            "travel application bootstrapped (Phase C; amap disabled, capabilities=%s)",
            sorted(parts.capabilities),
        )
    return app


def _build_pipeline_parts(settings: Settings, capabilities, llm, *, checkpoint_store=None, user_repository=None) -> PipelineParts:
    caps = set(capabilities) if capabilities is not None else set(DEFAULT_CAPABILITIES)
    provider_llm = llm if llm is not None else OpenAICompatibleToolCallingLLM(settings)
    telemetry_repository = MySQLLLMTelemetryRepository(checkpoint_store.sessions) if isinstance(checkpoint_store, MySQLStateStore) else None
    quota_service = (QuotaService(MySQLQuotaRepository(checkpoint_store.sessions, default_new_user_points=settings.default_new_user_points), PricingRegistry())
                     if isinstance(checkpoint_store, MySQLStateStore) else None)
    real_llm = TelemetryLLMGateway(provider_llm, telemetry_repository, quota_service=quota_service, model=settings.llm_model,
                                   reserve_prompt_tokens=settings.quota_reserve_prompt_tokens,
                                   reserve_completion_tokens=settings.quota_reserve_completion_tokens)

    tools = ToolRegistry()
    tools.register(OpenMeteoWeatherTool())

    registry = ExecutorRegistry()
    registry.register(BudgetPolicyExecutor(), {"budget_policy"}, agent_ids={"budget_policy"})
    # weather 研究：只暴露 open-meteo weather
    registry.register(
        ReActExecutor(
            "react-weather",
            real_llm,
            tools,
            AgentProfile("weather-agent", {"weather_research"}, {"weather"}, max_steps=3, max_tool_calls=2),
        ),
        {"weather_research"}, agent_ids={"weather_agent"},
    )
    # 保留合成 DAG 节点供前端展示“研究完成→最终行程生成”的交接，
    # 但不再让它用 LLM 重写一次研究结论。TripPlanSynthesizer 是唯一行程生成 LLM。
    if "itinerary_synthesis" in caps:
        registry.register(
            EvidenceHandoffExecutor(),
            {"itinerary_synthesis"}, agent_ids={"itinerary_agent"},
        )

    checkpoint_store = checkpoint_store or SQLiteStateStore(settings.resolve_db_path())
    worker_pool = GlobalWorkerPool.homogeneous(
        size=settings.agent_worker_pool_size,
        capabilities=registry.capabilities,
        max_queue_size=settings.agent_worker_queue_size,
    )
    runtime = GraphRuntime(registry, checkpoint_store, worker_pool=worker_pool)

    intent_service = IntentService(LLMIntentParser(real_llm))
    routing_service = RoutingService(LLMComplexityAnalyzer(real_llm), ComplexityRouter())
    planning_service = PlanningService(LLMStructuredPlanner(real_llm), PlanBuilder(), registry=registry)

    memory_service = MemoryService(user_repository or SQLiteUserRepository(settings.resolve_db_path()))
    processor = TravelPipelineProcessor(
        intent_service=intent_service,
        routing_service=routing_service,
        planning_service=planning_service,
        runtime=runtime,
        llm=real_llm,
        capabilities=caps,
        synthesizer=TripPlanSynthesizer(
            real_llm,
            image_resolver=AttractionImageResolver(
                unsplash_access_key=settings.unsplash_access_key,
                baidu_service=BaiduImageService(
                    enabled=settings.baidu_image_enabled,
                    endpoint=settings.baidu_image_endpoint,
                ),
            ),
        ),
        evaluator=DeterministicTripEvaluator(),
        replanning_service=ReplanningService(PlanBuilder()),
        memory_service=memory_service,
        execution_budget=DEFAULT_EXECUTION_BUDGET,
        max_repair_attempts=1,
    )
    return PipelineParts(
        processor=processor,
        llm=real_llm,
        tools=tools,
        executor_registry=registry,
        runtime=runtime,
        worker_pool=worker_pool,
        capabilities=caps,
    )


__all__ = ["BootstrapError", "DEFAULT_CAPABILITIES", "build_travel_app"]




