# trip-planner 迁移：审计核实报告 + Phase A/B 实施清单

> 生成时间：2026-09-05 · 方式：静态代码核对（只读，未改动任何文件）
> 核对范围：`trip-planner/backend`（全量精读）、`trip-planner/frontend`（子代理核对 Home.vue/Result.vue/api.ts/types）、`travel_agent/agent`（整树精读）、`travel_agent/evals|tests|config`（子代理核对）
> 结论：审计主体结论成立，少数表述需修正（见 §1 末「三处修正」）。据此细化出 Phase A/B 可执行清单（§4、§5）。

---

## 0. 现状调用链（与审计一致，已逐行复核）

```text
Home.vue handleSubmit (Home.vue:246)
→ api.ts generateTripPlan (api.ts:43)  POST /api/trip/plan, timeout 120s, baseURL = VITE_API_BASE_URL || http://localhost:8000 (api.ts:4)
→ routes/trip.py plan_trip (trip.py:20, async def 内直接调同步阻塞 plan_trip)
→ agents/trip_planner_agent.py MultiAgentTripPlanner.plan_trip (L223，固定顺序景点→天气→酒店→规划)
→ 各 SimpleAgent.run（文本式 [TOOL_CALL:...]，只取第 1 个偏好，见 L291）
→ _parse_response (L328，JSON 提取，失败回 fallback)
→ TripPlan → TripPlanResponse(success=True)  ← fallback 也 success=True
→ sessionStorage('tripPlan')  ← Home.vue:294 写入
→ Result.vue:330 读取展示
```

真实执行唯一入口（travel_agent 侧）：`evals/run_execution_online.py`（intent→routing→planning→runtime 全真实装配，仅此处）。

---

## 1. 审计结论逐条核实

### 1.1 trip-planner 后端（本人精读）

| # | 审计断言 | 结论 | 证据（文件:行号） |
|---|---|---|---|
| T1 | 固定四角色，非动态任务图 | ✅ 成立 | `trip_planner_agent.py:179-210` 顺序创建；`plan_trip L244-263` 顺序写死 |
| T2 | 三个 Agent 未按职责收缩工具权限，共享同一个 MCPTool | ✅ 成立 | `L168-202` 同一 `self.amap_tool` 加到三个 agent |
| T3 | 景点查询文本式 `TOOL_CALL` 只使用第一个偏好 | ✅ 成立 | `L281-292`，`keywords = request.preferences[0]` |
| T4 | 补充文本仅在最终规划 Prompt 才加入 | ✅ 成立 | `L323-324` |
| T5 | async API 内调同步阻塞代码 | ✅ 成立 | `trip.py:20` async，`L44` 直接 `agent.plan_trip(request)`（同步、内部还多次跑 LLM/MCP） |
| T6 | MCP 初始化双路径（Agent 内 + amap_service） | ✅ 成立 | Agent 内 `L168-174`；`amap_service.py:28-34` 另一 MCPTool，命令/Key 相同 |
| T7 | 主流程异常返回 fallback | ✅ 成立 | `trip_planner_agent.py:275-279` |
| T8 | JSON 解析失败返回 fallback | ✅ 成立 | `L366-369` |
| T9 | fallback 造占位景点 + 与城市无可靠关系的坐标 | ✅ 成立（更严重） | `L389-399`：景点名 `{city}景点{j+1}`；坐标**写死北京起点** `116.4+i*0.01+j*0.005, 39.9+...`，与请求城市完全无关 |
| T10 | fallback 仍 `success=True` 返回 | ✅ 成立 | `trip.py:48-52`（route 层无失败分支）；fallback 只在 agent 内部消化 |
| T11 | amap_service 空解析分别返回 `[]/[]/{}/None` | ✅ 成立 | `amap_service.py:86-87`（`# TODO` 后 return []）、`115-116`、`181-182`、`212-213` |
| T12 | POI detail 返回未解析 raw 或 {} | ✅ 成立 | `L244-254`：`{"raw": result}` 或 `{}` |
| T13 | 无法解析的温度归零 | ✅ 成立 | `schemas.py:119-130` validator `except ValueError: return 0`；Budget/费用类默认 0（L72/82/94/135-139）→「0=未知」掩盖问题确认 |
| T14 | route 返回 `{}` 会触发响应模型校验错误 | ✅ 成立 | `map.py:128` `RouteResponse(data={})`；`schemas.py:189` `data: Optional[RouteInfo]` → `{}` 不合法 |
| T15 | trip 健康检查访问 `agent.agent`，与实际四 Agent 属性不符 | ✅ 成立（必现 503） | `trip.py:78`；`MultiAgentTripPlanner` 无 `.agent` 属性（属性是 attraction_agent/…）→ AttributeError → HTTPException 503 |
| T16 | 顶层 /health 不代表 Provider/MCP 可用 | ✅ 成立 | `main.py:80-87` 仅返回静态字段 |
| T17 | 跨项目加载 HelloAgents/.env | ✅ 成立 | `config.py:14-16` 从 `backend/app/config.py` 向上四级拼 `HelloAgents/.env`（= `D:\learning\LLM\HelloAgents\.env`） |
| T18 | LLM 配置命名不统一（LLM_MODEL_ID vs LLM_MODEL、openai_model 混用） | ✅ 成立 | `config.py:105` 用 `LLM_MODEL_ID`；`llm_service.py:24` `HelloAgentsLLM()` 自行读环境变量（settings 里 openai_* 并未真正传给 LLM）；travel_agent 用 `LLM_MODEL`（`config.py:13`） |
| T19 | 健康检查契约错误 / map 健康检查访问私有成员 | ✅ 成立 | `map.py:156` `service.mcp_tool._available_tools` 私有访问 |
| T20 | 同步 SQLite/同步 Agent 阻塞 async 服务 | ⚠️ 部分成立（SQLite 当前无） | Agent 同步阻塞已确认；后端目前**没有** SQLite，属 Phase B/F 引入时须规避的风险点 |

### 1.2 trip-planner 前端（子代理核对，含两处措辞修正）

| # | 审计断言 | 结论 | 证据 |
|---|---|---|---|
| F1 | Home.vue 假进度 setInterval | ✅ 成立 | `Home.vue:256-272`（500ms +10，封顶 90），成功后 L289 置 100 |
| F2 | 表单→TripFormData→generateTripPlan | ✅ 成立 | `Home.vue:275-286` |
| F3 | sessionStorage 唯一状态源 | ✅ 成立 | key=`tripPlan`：写 `Home.vue:294`，读 `Result.vue:330`，编辑保存重写 `Result.vue:367` |
| F4 | 硬编码 localhost「图片 URL」 | ⚠️ 措辞修正 | 仅 1 处：`Result.vue:435` `fetch('http://localhost:8000/api/poi/photo?...')` 是**取图接口 base 硬编码**；真正 `<img :src>`（L189）来自后端返回的 `data.data.photo_url`，并非硬编码 |
| F5 | 地图为景点坐标间 Polyline 直线 | ✅ 成立 | `Result.vue:940-951`；无坐标景点不画 marker（L868 过滤）→ 成立 |
| F6 | types 无 confidence/provenance/status | ✅ 成立 | `types/index.ts` 字段为展示字段 |
| F7 | 无 SSE/事件式进度/澄清交互 | ✅ 成立 | 全 src 无 EventSource/WebSocket |
| F8 | vite proxy `/api`→8000 | ✅ 成立 | `vite.config.ts:13-21` |

### 1.3 travel_agent 核心（本人整树精读，共 ~50 文件 / 1076 行）

| # | 审计断言 | 结论 | 证据 |
|---|---|---|---|
| G1 | RequirementAnalysis 强制 destination+travel_time，阻断无日期 DIRECT | ✅ 成立 | `elicitation/analysis.py:16-22`：`missing`=destination/travel_time → `blocking_fields=missing` → `clarification_needed`；无论意图是否为「只问一句事实」。**但注意：destination 有即算数，日期(travel_time)是真正硬门槛** |
| G2 | IntentNormalizer 固定年份 | ✅ 成立 | `intent/normalize.py:7` `year: int = 2026` 硬编码默认 |
| G3 | merge 支持 correction/preservation | ✅ 成立 | `intent/merge.py:9-24`：list 合并、correction_fields 替换、已显式事实不被非 correction 冲突覆盖 |
| G4 | canonical normalizer `bool("false")` 为真 | ✅ 成立 | `routing/normalize.py:10` `d[k]=bool(d[k])`；同 L8 `int()` 对非法串直接抛错（未捕获） |
| G5 | LLMComplexityAnalyzer 真实 Provider 路径 | ✅ 成立 | `routing/llm_analyzer.py:7` `generate_structured(..., SemanticComplexitySignals)` |
| G6 | ComplexityRouter 确定性、不看原始 query、不选工具/Agent/不调 Planner | ✅ 成立 | `routing/policy.py:5-20` 仅基于 signals |
| G7 | PlanningService 要求 COMPLEX | ✅ 成立 | `app/planning_service.py:7` |
| G8 | PlanBuilder graph_id/version 固定为 v1 | ✅ 成立 | `planning/builder.py:12` `graph_id=f"{session_id}-v1", version=1` 写死（TaskGraph 本身有 version 字段 `domain/models.py:30`，只是没人用它递增） |
| G9 | 旧 app/service.py 与当前 Protocol 不兼容 | ⚠️ 部分成立 | `app/service.py:6-7` `planner.plan(intent)` 单参 vs `Planner.plan(intent, context)`（`planning/base.py:4`）；但 FakePlanner/UserIntent 等类仍存在，「已不存在的类」不成立；仅 `tests/test_runtime.py:121` 引用 |
| G10 | Runtime 按 ready 批次 gather，非事件驱动 | ✅ 成立 | `runtime/graph_runtime.py:40-48`：每轮取 ready → 全部 RUNNING → `asyncio.gather` 同批；快任务下游等慢任务 |
| G11 | 每任务完成即 checkpoint | ✅ 成立 | `graph_runtime.py:80-84` `_execute_and_save` |
| G12 | INVALIDATED 是终态，调度只选 PENDING → 不能原地重规划 | ✅ 成立 | `graph_runtime.py:11-17` 迁移表 `INVALIDATED: set()`；`_ready_tasks L59` 只取 PENDING |
| G13 | SQLite 按 session_id 只留最新快照，无版本历史 | ✅ 成立 | `state_store.py:24-28` 单表 `session_id PRIMARY KEY` + UPSERT |
| G14 | Recovery 只支持同图 Task-boundary | ✅ 成立 | `runtime/recovery.py:8` 图 id/version 必须一致；仅 RUNNING→PENDING（L12-14） |
| G15 | ReAct 真实工具循环 | ✅ 成立 | `executors/react.py:66-102`：schema→decide→tool_call→observation→role=tool 回填 |
| G16 | ReAct 只暴露 READ_ONLY 工具 | ✅ 成立 | `react.py:33-41` |
| G17 | ReAct 成功判定偏宽松 | ✅ 成立 | `react.py:75-77`：只要 `decision["final"] is not None` 即 success=True（即使必需工具失败/证据缺失） |
| G18 | 上游 TaskResult 进上下文 | ✅ 成立 | `react.py:29-30,51-52`；`ExecutionContext.dependency_results`（`graph_runtime.py:23-27`）只含直接父任务 |
| G19 | 动态 JSON schema 验证缺口（仅 input_model 时验证） | ✅ 成立 | `tools.py:19` 仅 `if tool.spec.input_model` 走 pydantic；`react.py:38-39` 组装 schema 时有 `input_schema` fallback，但 registry.call 不会校验它 |
| G20 | llm.py 有 tool-call/structured/timeout/usage | ✅ 成立 | `llm.py:70-81`(decide)、`83-110`(structured, 2 次修复)、超时 L55 |
| G21 | 并发 usage 归因隔离风险 | ✅ 成立（潜在） | `llm.py:34` `self.usage`/`self.structured_attempts` 为实例共享可变态，`_request L40` 每次重置 → 实例被并发/多阶段复用时归因错乱（`run_execution_online.py:98-108` 每阶段后快照掩盖了该问题） |
| G22 | 天气工具仅当前、拒绝指定日期 | ✅ 成立 | `weather.py:10-11` `UNSUPPORTED_DATE` |
| G23 | Evaluator 只查 TaskResult.success | ✅ 成立 | `evaluation/fake.py:3-5`；真实 Evaluator 不存在（`evaluation/base.py` 仅 Protocol） |
| G24 | 真实链路只在 eval runner 装配 | ✅ 成立 | `evals/run_execution_online.py:94-111`；`CAPABILITIES={'weather_research','itinerary_synthesis'}`（L29）；无顶层生产入口（`__main__` 仅 4 个 run_*.py；pyproject 无 [project.scripts]） |
| G25 | eval runner 有 max_steps/超时/修复上限，无 token/总时长预算 | ✅ 成立 | `run_execution_online.py:37` max_steps=6；`llm.py:55` timeout；修复 2 次（L90）；无 token/wall budget |
| G26 | travel_agent 配置读取集中 | ⚠️ 修正审计 | TA 侧仅 `config.py:7-13` LLMConfig.from_env 一处；**审计把 trip-planner 的双源问题误并入 TA**。TA 无「多处直读/打印不一致」问题 |
| G27 | conftest 只 patch urllib | ✅ 成立（缺口为机制性） | `tests/conftest.py:14`；TA 现网代码仅 urllib（llm.py/weather.py），暂无 httpx 载体 → 迁入后引入 httpx 时必须扩展隔离 |
| G28 | online 测试用 RUN_ONLINE_TESTS gating | ✅ 成立 | `test_llm_online.py:5,8` / `test_router_online.py:7,10` / `test_planner_online.py:15,19-20`；`test_llm_planner.py` 是 fake 非 online（审计归类有误但影响小） |

### 1.4 三处需修正的审计表述

1. **F4**：不是「写死 localhost 图片 URL」，而是 Result.vue 仅有 1 处取图接口 base 硬编码（`Result.vue:435`），图片 src 本身来自后端返回字段。
2. **G26**：TA 配置读取集中无问题；「配置来源不一致」问题只存在于 trip-planner（`config.py` + `llm_service.py` HelloAgentsLLM 自读 + print_config 三处）。
3. **G28**：`test_llm_planner.py` 是离线 fake 测试，不属 online 组。

---

## 2. 关键决策点（需你确认后再实施）

| # | 决策 | 选项 A | 选项 B | 我的建议 |
|---|---|---|---|---|
| D1 | 迁移期旧 `/api/trip/plan` 处理 | 双轨：旧路径保留在 `ENABLE_LEGACY_PLANNER=true` 显式配置后，新 `/api/travel/*` 走新应用服务；D 阶段默认关、H 阶段删除 | 立即切换：`/api/trip/plan` 转新服务，COMPLEX 未完成前返回明确 failed（前端行程功能暂时不可用） | **A**：保证每阶段产品可用、可回滚；旧路径仅在 flag 后存在，永不静默兜底新链路 |
| D2 | Phase B 真实组件范围 | 先只接 weather 工具 + urllib Provider（同 TA eval 验证过的组合），AMap MCP 留 C 阶段 | B 阶段就换 MCPToolAdapter | **A**：MCP 适配器（Option B in audit §9）契约复杂，放在 C 单独验收更稳 |
| D3 | LLM 环境变量命名 | 统一为 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL/LLM_TIMEOUT_SECONDS`；旧名（LLM_MODEL_ID/LLM_TIMEOUT/OPENAI_API_KEY）做兼容映射 + deprecation warning | 保留 trip-planner 现有命名 | **A**（与 travel_agent 一致，单一事实源） |
| D4 | DIRECT 被日期阻断的修法 | 给 intent 增加 `planning_intent: bool` 信号，RequirementAnalyzer 只对 planning 类请求要求 destination/time | 直接放宽所有意图的日期要求 | **A**：字段级信号最可控；DIRECT 问答仍需 destination 常识？——见 §5.3 细化 |
| D5 | Phase A 的 `/api/trip/plan` 内部语义 | 作为「兼容同步入口」只透传到 runs 应用服务，等待单轮结果（120s 内） | Phase A 不接 trip/plan，只新增 runs API | **A**：兼容入口从第一天就转发新应用层，避免「新 API 是摆设」 |

---

## 3. 目标边界（Phase A/B 结束时的验收口径）

- Phase A 结束：浏览器/curl 打到的新入口（`/api/travel/*` + trip/plan 转发路径）**确实调用** `TravelApplicationService`；配置缺失→明确不可用（不是 fake）；健康检查反映真实装配；typed 响应 `clarification | answer | trip_plan | failed`；旧固定四 Agent 只在显式 flag 后可触达。
- Phase B 结束：真实 `IntentService→RoutingService→PlanningService→(Runtime+ReAct+SQLite)` 已迁入 trip-planner 包内并可装配运行；DIRECT 不被无关字段阻断；三条 route 正确分流；Task-boundary checkpoint + 同图恢复接通；**无 fake 进入生产装配**；default pytest 全离线。
- 本阶段**不做**：真实 MCP/AMap（C）、TripPlan 合成（D）、真实 Evaluator（E）、局部重规划/版本历史（F）、SSE/进度 UI（G）、E2E 收尾（H）。清单中的「预留点」会在后续阶段展开。

---

## 4. Phase A 实施清单：生产装配 + API 边界

### 4.1 改动文件（精确到行）

**`backend/app/config.py`（重构，~111 行 → ~150 行）**
- 删除：`L13-16` HelloAgents/.env 跨目录加载。
- 保留 `load_dotenv()` 但指向单一 `backend/.env`；`pydantic-settings` 配置改为显式字段：
  - 保留：app_name/app_version/host/port/cors_origins/log_level/amap_api_key/unsplash_*。
  - LLM 统一为：`llm_api_key: SecretStr|None`、`llm_base_url: str|None`、`llm_model: str|None`、`llm_timeout_seconds: float = 30`（env: `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL/LLM_TIMEOUT_SECONDS`）。
  - 新增：`enable_legacy_planner: bool = False`（D1）、`db_path: str = "data/travel_runs.sqlite"`、`max_run_steps...`（B 预算预留，可空）。
- 兼容映射（放在 settings 构造后）：旧 `LLM_MODEL_ID` → llm_model、`LLM_TIMEOUT` → timeout、`OPENAI_API_KEY/OPENAI_BASE_URL` → 对应新字段，命中即打印 `deprecation warning`；新旧同 key 冲突时报错（不静默）。
- `validate_config()` 语义分层：
  - core（LLM 三件套）缺失 → raise（新链路不可静默降级）；
  - amap_api_key 缺失 → 仅当 enable_legacy_planner 或调用 map/poi 时告警（B/C 阶段 map 工具上线后转 error）。
- 提供 `config_loaded_from` 只读标识供测试断言「未加载跨项目 .env」。

**`backend/app/api/main.py`（小改，~99 行）**
- `on_event("startup")`（L35-57）改为 FastAPI `lifespan`：在其中调用 `bootstrap.build_travel_app(settings)` 并把实例挂到 `app.state.travel_app`；启动失败即 raise（配置缺失不静默）。
- 路由挂载：保留 trip/poi/map router；新增 `runs` router（`prefix="/api/travel"`）；`/health` 增加 `app.state.travel_app` 可用性字段（不触发任何 provider 连接）。
- 删除 `startup_event` 里会 raise 的旧 `validate_config()` 调用方式 → 归入 lifespan。

**`backend/app/api/routes/trip.py`（重构，~86 行）**
- `plan_trip`（L20-61）改为薄转发：
  - `settings.enable_legacy_planner=True` → 临时保留现有实现，响应 message 加 `[deprecated]`；**不新增任何 fallback 改动**。
  - `False` → 组装 `TripFormData→自然语言消息`，调 `app.state.travel_app.run_single_plan(...)`；按 typed result 返回：`trip_plan → TripPlanResponse(success=True)`；`clarification → success=False + 返回澄清问题`（前端可先展示 message）；`answer/failed → success=False + 明确错误`。**任何路径不得再返回占位行程/虚构坐标**。
- `health_check`（L69-85）修复：移除 `agent.agent.name/list_tools()`；改为检查 `app.state.travel_app` 装配状态 + llm 配置项存在性，不再实例化四 Agent。

**`backend/app/services/llm_service.py`（暂留，37 行）**
- 仅在 legacy 路径使用；`get_llm()` 增加 docstring 标注 deprecated；Phase C 移除时随旧路径删除。不要让它进入新装配。

**`backend/app/services/amap_service.py` / `unsplash_service.py`**
- Phase A 不动实现（C 阶段换 adapter）；`amap_service` 顶部加 deprecated 注释，说明 C 阶段由 `tools/mcp/*` 替代。

**`backend/app/api/routes/map.py:156` / `poi.py`**
- Phase A 仅把 `map.py /health` 的私有 `_available_tools` 访问替换为公开计数（`len(service.mcp_tool.list_tools())` 若 lib 支持，否则 try/except 包住），避免健康检查把私库契约泄漏进产品。poi.py 不动。

### 4.2 新增文件

**`backend/app/bootstrap.py`（唯一生产装配根，NEW）**
- `build_travel_app(settings) -> TravelApplication`：只装配真实组件（LLM adapter、parser、services、空 Tool/Executor Registry、SQLiteStateStore(db_path)）；**无任何 Fake/自动降级**；任一必需组件构造失败直接 raise。
- 依赖注入契约：`build_travel_app(settings, *, intent_parser=None, ...)` 参数化，测试注入替身；生产不传。
- 生命周期：注册 `async close()`（关闭 provider 资源/数据库句柄；MCP 连接 C 阶段接入）。
- 同时提供 `create_app()`（FastAPI app 工厂，替代 main.py 全局 `app`），保持 `app.api.main:app` 导入兼容（`app = create_app()`）。

**`backend/app/application/travel_service.py`（NEW）**
- 数据契约（Phase A 最小版）：
  - `RunStatus`: `pending/running/completed/failed`；
  - `RunResult.kind`: `clarification | answer | trip_plan | failed`（discriminator，供前端区分结果类型）；
  - `RunRecord`: run_id、messages、status、result、events（列表，占位）、created/updated。
- 接口（async）：
  - `create_run(message, session_meta) -> RunRecord`（入库）
  - `handle_message(run_id, message) -> RunResult`（Phase A：走 IntentService→RequirementAnalysis；READY 且暂无可执行分支 → 明确 failed(`"routing not yet available (Phase B)"`)，**绝不伪造**）
  - `get_run(run_id)` / `list_events(run_id)`（供后续 G 阶段 SSE，先落库事件模型）
  - `run_single_plan(request) -> RunResult`（trip/plan 兼容入口复用）
- Phase A 真实闭环语义：NEEDS_CLARIFICATION→返回澄清问题；OUT_OF_DOMAIN→明确返回；CONFLICT→返回冲突；READY→failed(未实现路由) 直到 B 阶段接通 routing/planning。
- 说明：DIRECT 无工具回答、SIMPLE、COMPLEX 真行程为 B/D 阶段职责，A 阶段以类型化失败结束，**不允许**回退旧 Agent 或假数据。

**`backend/app/persistence/__init__.py` + `run_repository.py`（NEW，最小版）**
- SQLite 表 `runs`（run_id PK, session_meta, status, created_at…）+ `run_events`（seq, event, payload）轻量存储；StateStore 迁移沿用 TA（B 阶段合入）。
- 同步 sqlite3 用 `asyncio.to_thread` 包裹（规避审计 T20 阻塞风险）。

**`backend/app/api/routes/runs.py`（NEW）**
- `POST /api/travel/runs`、`GET /api/travel/runs/{run_id}`、`GET /api/travel/runs/{run_id}/events`、`POST /api/travel/runs/{run_id}/messages`（Phase A 即可用，为澄清多轮/G 阶段 SSE 打底）。响应统一 typed envelope。

**`backend/tests/`（首次引入 pytest 基础设施）**
- `tests/conftest.py`：autouse fixture 默认全离线（patch urllib.request.urlopen + 阻断子进程外联；httpx 载体从第一版就 patch，避免 TA 只 patch urllib 的缺口带进来）；`online` marker + `RUN_ONLINE_TESTS=='1'` gating（沿用 TA 语义）。
- `tests/fakes/`：集中测试替身（FakeLLM/FakeIntentParser/FakeTool…），生产启动代码不得 import 此目录。
- `tests/test_config.py`：env 映射/旧名 deprecation/冲突报错/未加载跨项目 .env。
- `tests/test_bootstrap.py`：DI 装配、缺 key raise、无 Fake 自动 fallback。
- `tests/test_api_contract.py`：runs 建/查/事件、typed discriminator、trip/plan 转发失败为 `success=False`（无假行程）、health 不触发 provider、legacy flag 开关行为。

### 4.3 Phase A 验收

- [ ] curl `POST /api/travel/runs` 真实经过 `TravelApplicationService`（可断点/日志证明），不触碰 `MultiAgentTripPlanner`。
- [ ] `GET /health` 与 `/api/trip/health` 返回真实装配状态，不再有 `agent.agent` 崩溃。
- [ ] 缺 `LLM_*` 配置时启动 raise，行为明确，无 fake 兜底。
- [ ] `ENABLE_LEGACY_PLANNER=false` 时任何路径都不会返回占位景点/虚构坐标/`success=True` 假行程。
- [ ] default `pytest backend/tests` 全离线通过。

---

## 5. Phase B 实施清单：Agent 核心 + Checkpoint 迁入

### 5.1 目录与文件映射（`travel_agent/agent/...` → `trip-planner/backend/app/...`）

按审计目标目录树落位（包名从 `agent` 换为 `app` 内子包；TA 内部相对导入 `.`/`..` 因同层结构基本可保留，逐文件核对 import 即可）：

| 目标（trip-planner/backend/app/…） | 来源（travel_agent/agent/…） | 动作 | 备注 |
|---|---|---|---|
| `domain/intent.py` `routing.py` `planning.py` | `domain/intent.py` `routing.py` `planning.py` | COPY+改造 | `planning_intent` 字段新增见 §5.3 |
| `domain/models.py` | `domain/models.py` | COPY | TaskGraph 已含 version；INVALIDATED 语义保留 |
| `domain/execution.py` | `runtime/errors.py` + `runtime/state_store.py` 的协议 | 拆分 | `TaskStatus/Task/TaskGraph/TaskResult` 可留 models.py，error/state 协议分离 |
| `intent/base.py llm_parser.py normalize.py merge.py` | 同名 | COPY（排除 fake） | normalizer year 参数改注入 |
| `elicitation/analysis.py questions.py` | 同名 | COPY+改造 | §5.3 DIRECT 修复落点 |
| `routing/base.py policy.py llm_analyzer.py normalize.py errors.py` | 同名 | COPY（排除 fake） | normalizer 严格化（§5.3） |
| `planning/base.py capabilities.py llm_structured.py builder.py` | 同名 | COPY（排除 fake） | builder 增加 `version` 参数（F 预留） |
| `application/intent_service.py routing_service.py planning_service.py` | `app/intent_service.py` 等 | COPY | import 指向新 domain |
| `runtime/graph_runtime.py registry.py errors.py state_store.py recovery.py` | 同名 | COPY | state_store 保留（B 接 checkpoint）；版本历史 F 阶段扩 |
| `executors/base.py react.py` | 同名 | COPY | AgentProfile 保留在 react.py |
| `providers/protocols.py` | `llm.py` Protocol 段 | 拆分 | 契约独立成文件 |
| `providers/openai_compatible.py` | `llm.py` OpenAICompatible* | COPY | usage 归因小改见 §5.3 |
| `tools/base.py` | `tools.py` ToolSpec/ToolResult/SideEffectLevel/Registry | 拆分 | schema 校验缺口留 C（MCP adapter 一起） |
| `tools/weather.py` | `weather.py` | COPY | urllib → 隔离可 patch；forecast_date 拒绝语义保留 |
| `tests/` | TA 离线套件 | 移植 | §5.4 |

**不迁入**：`intent/fake.py routing/fake.py planning/fake*.py executors/fake.py evaluation/*`（生产）、`app/service.py`（legacy 单体）、`evals/*`（后续改为 import 生产 bootstrap）。

### 5.2 真实链路装配（bootstrap 扩展）

```text
TravelApplicationService
→ IntentService(LLMIntentParser(OpenAICompatibleToolCallingLLM), RequirementAnalyzer, ClarificationGenerator)
→ READY → RoutingService(LLMComplexityAnalyzer, ComplexityRouter)
   ├ DIRECT  → 无工具回答（B 阶段最小实现：LLM 直答 + 明确「无行程承诺」，计入 runs）
   ├ SIMPLE  → 保留为 COMPLEX 前置（先走同 COMPLEX 但限制预算）或 B 阶段明确 failed？→ 见验收口径
   └ COMPLEX → PlanningService(LLMStructuredPlanner, PlanBuilder) → TaskGraph
              → GraphRuntime(ExecutorRegistry[ReActExecutor], SQLiteStateStore)
              → run_events 落库（task_started/task_completed/graph_completed）
```
- capabilities：先按 TA `DEFAULT_CAPABILITIES` 全集注册（travel 语义：place/weather/food/hotel/transport 研究 + geospatial/budget reasoning + synthesis），Executor 用 ReActExecutor 覆盖全部（allowed_tools 白名单由任务决定，当前真实工具仅 weather + 后续 AMap）。
- SIMPLE 路由在 B 阶段：规划出 ≤N 任务图仍走 Runtime；实现与 COMPLEX 同构（仅图上界/预算不同），避免「三分支改名固定结构」（审计 §17 检查项）。
- 事件先写入 `run_events`（供 G 阶段 SSE），Phase B 不接前端。

### 5.3 迁入时必须落的小修复（清单内，不后拖）

| 修复 | 文件 | 改法 |
|---|---|---|
| DIRECT 阻断 | `domain/intent.py` + `intent/llm_parser.py` prompt + `elicitation/analysis.py` | UserIntent 增 `planning_intent: bool|None`；parser prompt 显式判定「是否要完整行程规划」；RequirementAnalyzer：`planning_intent=False` 时不要求 destination/travel_time（DIRECT 单问不被日期卡住）；`planning_intent=None` 保留澄清兜底 |
| 年份写死 | `intent/normalize.py` | `year` 由调用方传入（bootstrap 注入当前年/消息年份推断），删除默认 2026 |
| normalizer 严格化 | `routing/normalize.py` | `requires_*` 只接受 bool/「true/false」字面量白名单，其余置 ambiguity/报错；int/float 用 try 包裹并落 uncertainty |
| builder 版本参数 | `planning/builder.py` | `build(draft, context, version=1)`；graph_id=f"{session}-v{version}"（F 阶段发布 vN+1 的前置） |
| usage 归因 | `providers/openai_compatible.py` | 每请求返回独立 usage 快照，实例不再持有共享可变 `self.usage` 用于跨阶段累计（structured_attempts 改由调用方按 run 记录） |
| checkpoint 异步 | `runtime/state_store.py` SQLite | sqlite3 调用包 `asyncio.to_thread`（T20） |
| 恢复不跨版本 | `runtime/recovery.py` | 保留同图校验；新增 docstring 明确「恢复 vN 与发布 vN+1 是不同操作」（F 阶段实现后者） |
| DIRECT/SIMPLE 校验 | `application/travel_service.py` | DIRECT/SIMPLE 走轻量回答校验（有内容、不冒充 TripPlan），不强迫生成行程 |

### 5.4 Phase B 测试（移植 + 新增，均离线）

移植（改 import 到新包）：`test_normalize`、`test_signal_normalize`、`test_routing`、`test_react`、`test_runtime`、`test_recovery`、`test_planning`、`tests/integration/test_end_to_end_m1_m5.py`（fake 上游 + 真实 runtime/SQLite）。
新增：`test_intent_direct_not_blocked.py`（无日期 DIRECT 问答、planning_intent 各取值）；`test_normalizer_robust.py`（"false" 字面量/非法数字）；`test_builder_version.py`；`test_bootstrap_no_fake.py`；`test_api_runs_integration.py`（注入 fake LLM，API→M1→Routing→Planning→Runtime 全链路 typed 事件、任务依赖结果、checkpoint 恢复）。

### 5.5 Phase B 验收

- [ ] 三条 route 由真实信号 + 确定性 Router 分流，DIRECT 不被无关日期字段阻断。
- [ ] 复杂请求产出**结构化差异的任务图**（不同 intent → 不同 TaskGraph），不是固定三分支改名。
- [ ] Planner 只出 PlanDraft；Runtime 按依赖执行；下游实际消费上游 TaskResult。
- [ ] ReAct 真实收到 tool schema、执行工具、读取 observation（weather 工具真实可用）。
- [ ] checkpoint 每任务落库；同图恢复可用；`run_events` 已记录任务级事件。
- [ ] 生产装配无 Fake、无旧 `TravelPlanningService`、无 eval-runner 自建依赖。
- [ ] default pytest 全离线；`RUN_ONLINE_TESTS=1` 冒烟（weather 真实）可 opt-in 跑通。

---

## 6. 依赖 / 配置 / 风险表

### 6.1 依赖变化（后端）
- 新增（dev）：`pytest>=8.0`、`pytest-asyncio>=0.24`（对齐 TA dev）；`httpx` 已在 requirements（测试 Client 用）。
- 保留：`hello-agents`（legacy 路径，Phase C/H 移除）；`pydantic>=2.0`（TA 核心唯一运行时依赖，已满足）。
- 不动 `uvx amap-mcp-server` 的既有用法（C 阶段由 adapter 管理）。

### 6.2 环境变量最终口径（backend/.env）
```
LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT_SECONDS   ← 唯一事实源（D3）
AMAP_API_KEY（传给 MCP 子进程时映射为 AMAP_MAPS_API_KEY，仅后端可见）
VITE_API_BASE_URL / VITE_AMAP_WEB_JS_KEY                         ← 前端浏览器侧，互不混用
ENABLE_LEGACY_PLANNER=false                                       ← 迁移期开关（D1）
DB_PATH（可选）
```
删除 HelloAgents/.env 自动加载；任何 `VITE_*` 不承载 LLM/server key。

### 6.3 风险与缓解（A/B 阶段）
| 风险 | 缓解 |
|---|---|
| 旧路径仍被前端默认使用 → 新链路无真实流量 | D1 默认双轨但新 runs API 立即可用；Phase B 结束把 Home.vue 指向 runs（G 阶段正式切 SSE），C/D 阶段起 `ENABLE_LEGACY_PLANNER` 默认 false |
| TA 文件迁入后 import 断裂（agent. 前缀→app.） | 按 §5.1 映射逐文件核对；迁入后立即跑移植测试确认 |
| urllib 隔离在 httpx 载体下失效 | conftest 从第一天同时 patch urllib + 阻断 httpx（`httpx.MockTransport`/网关） |
| DIRECT 语义被放宽导致误答 | `planning_intent` 由 LLM 结构化输出判定并保留 ambiguity；DIRECT 回答带「非行程建议」边界文案 |
| ReAct「final 即成功」宽松判定带入 B | 本阶段接受（B 验收不含质量评估），E 阶段 Evaluator 补；react.py 注释标记待改 |

---

## 7. 决策确认记录（2026-09-05 用户已拍板）与开工顺序建议

已确认（均按「我的建议」）：
- **D1+D5**：双轨 + `ENABLE_LEGACY_PLANNER` 开关；`false` 时 `/api/trip/plan` 转发新应用层，缺功能返回类型化失败（无假数据兜底）。
- **D2**：Phase B 只接 weather 工具（urllib Provider）；AMap MCP 适配器留 Phase C。
- **D3**：LLM 环境变量统一为 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL/LLM_TIMEOUT_SECONDS`；旧名兼容映射 + deprecation warning。
- **D4**：UserIntent 增加 `planning_intent` 信号；RequirementAnalyzer 只对 planning 类请求要求 destination/travel_time。

1. （已确认，D1–D5 无需再议）
2. 开工顺序建议：**Phase A（config+bootstrap+runs API+trip 转发+修复 health）→ A 验收测试 → Phase B（§5.1 映射迁入 → §5.3 小修复 → §5.4 测试 → B 验收）**，一次只提交一个 Phase。
3. Phase A 落地时我可以在你确认后直接改代码；若想先看某一具体文件的改写样例（例如 config.py 或 bootstrap.py 的完整实现），说一声即可。
