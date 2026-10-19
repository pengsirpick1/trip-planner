# 智能旅行规划 Agent Harness 与图编排系统

面向多轮旅行规划场景的 Agent 应用。项目不依赖 LangGraph 或 HelloAgents 作为生产运行时：LLM 负责理解需求和生成结构化任务草案，业务代码负责图校验、依赖调度、状态持久化、工具调用和局部重规划。

## 核心设计

```text
用户请求 / 历史偏好
        │
        ▼
意图解析与复杂度路由
        │
        ▼
结构化任务草案 ──► 任务图编译与校验
                         │
                         ▼
               DAG Runtime（就绪节点调度）
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  ReAct 研究节点    本地工具调用      MCP 工具调用
        │                │                │
        └────────────────▼────────────────┘
                         │
                         ▼
     共享执行状态 / Checkpoint / 局部重规划
                         │
                         ▼
                 行程合成、评估与 SSE 事件
```

### Agent Harness 职责

- **任务控制**：将 LLM 生成的任务草案编译为受控 DAG，校验依赖、环路、能力约束和终止条件。
- **图运行时与共享黑板**：统一维护任务图、节点状态、结构化结果、局部上下文及图版本；只调度依赖已满足的节点。
- **有界并发执行**：通过单机进程内的通用执行池，按任务能力匹配并轮询分配多个会话的就绪任务。
- **上下文与工具运行时**：节点仅获取本任务所需的用户约束和上游结构化证据；ReAct 节点可在步骤预算内决定工具及参数，统一接入本地工具和 MCP 工具。
- **可靠性**：节点边界保存 Checkpoint；版本 CAS 检测并发快照覆盖；任务失败或用户修改时，仅失效受影响节点及其下游，复用其余成功结果。
- **多用户与可观测性**：Bearer 认证、用户级会话与偏好记忆、LLM 调用遥测、配额账本、SSE 运行事件及前端执行轨迹展示。

## 技术栈

- **后端**：Python、FastAPI、asyncio、SQLAlchemy Async、Alembic、MySQL、Redis
- **Agent Runtime**：自定义 TaskGraph / DAG Runtime、共享黑板、ReAct、MCP、OpenAI-compatible LLM API
- **前端**：Vue 3、TypeScript、Vite、Ant Design Vue、高德地图 JavaScript API、SSE
- **基础设施**：Docker Compose（MySQL 8、Redis 7）

## 项目结构

```text
trip-planner/
├── backend/
│   ├── app/
│   │   ├── application/    # 意图、路由、规划、局部重规划与应用服务
│   │   ├── runtime/        # DAG 调度、Checkpoint、恢复与执行池
│   │   ├── executors/      # ReAct、预算与证据交接执行器
│   │   ├── tools/          # 本地工具、MCP 客户端、工具池与错误诊断
│   │   ├── persistence/    # MySQL / SQLite 仓储与 SQLAlchemy 模型
│   │   ├── telemetry/      # LLM 调用与尝试级用量记录
│   │   ├── quota/          # 额度预占、结算、对账与幂等账本
│   │   └── api/            # FastAPI、认证、Run、SSE、凭证接口
│   ├── migrations/         # Alembic migrations
│   ├── benchmarks/         # 可复现离线工程基准
│   └── tests/
├── frontend/
│   └── src/                # 规划表单、结果地图、Agent 轨迹与认证界面
├── docs/
└── docker-compose.yml
```

## 快速开始

### 1. 启动 MySQL 与 Redis

```bash
docker compose up -d mysql redis
```

### 2. 配置并启动后端

```bash
cd backend
copy .env.example .env          # PowerShell 也可使用 Copy-Item
```

在 `backend/.env` 中至少配置：

```dotenv
LLM_API_KEY=your_provider_key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model-name
AMAP_API_KEY=your_amap_server_key
DATABASE_URL=mysql+asyncmy://trip_planner:trip_planner_dev@127.0.0.1:3307/trip_planner
AUTH_SECRET_KEY=replace-with-a-random-32-plus-character-secret
```

随后安装依赖、执行迁移并启动：

```bash
pip install -r requirements.txt
alembic upgrade head
python run.py
```

后端文档：<http://localhost:8000/docs>

### 3. 配置并启动前端

```bash
cd ../frontend
copy .env.example .env
npm install
npm run dev
```

访问 <http://localhost:5173>。

> Windows 下使用 MCP stdio 时，请不要开启 Uvicorn `--reload`；该项目的 `python run.py` 已处理对应事件循环差异。

## 验证与基准

离线回归：

```bash
cd backend
python -m pytest --basetemp="$env:TEMP\trip_planner_pytest"
```

项目提供的基准明确区分受控离线指标与真实外部调用：

- 在 20 组固定需求变更的离线回放中，局部重规划相对全图重跑减少 **65.8%** 的节点重执行量，以及 **79.0%** 的模拟工具调用量。
- 该指标验证依赖失效范围、结果复用和图有效性；不将离线模拟结果表述为真实 LLM Token、线上 API 成本或模型质量。

## 兼容路径说明

`backend/app/agents/trip_planner_agent.py` 保留了早期 HelloAgents 固定多 Agent 原型，仅供兼容与迁移对照。它默认不会被生产链路加载；只有显式设置 `ENABLE_LEGACY_PLANNER=true` 时，旧的 `/api/trip/plan` 兼容分支才会启用。

当前默认生产路径是：

```text
/api/travel/runs
→ TravelApplicationService
→ 结构化规划与图编译
→ GraphRuntime
→ ReAct / 本地与 MCP 工具
→ Checkpoint、评估、局部重规划与 SSE
```

## 安全说明

不要提交 `.env`、真实 API Key、数据库文件或本地运行产物。仓库只保留 `.env.example` 作为配置模板；用户自带模型 Key 时，服务端使用 AES-256-GCM 加密保存，列表接口不会返回明文。

## 许可证

CC BY-NC-SA 4.0
