# trip-planner 实现验收报告

日期：2026-09-06。结论：**已接入真实动态 Agent 主链路，但尚未满足此前完整产品设想，不能验收为完整闭环。**

## 验收依据与范围

依据此前“旅游Agent架构设计”任务确定的目标：Vue → 唯一应用入口 → Intent/复杂度路由 → 动态 Planner/PlanBuilder/GraphRuntime → ReAct/真实工具 → Synthesis → TripPlan → Evaluator → 问题驱动的局部重规划；包括依赖传递、并发、持久化恢复和真实过程展示。另参考本项目 migration_plan_phase_ab.md 中已确认的阶段决策。

本轮阅读当前实现、运行前端构建、后端原有测试、独立行为探针，并通过生产 bootstrap 发起一次真实 DeepSeek＋高德 MCP 请求。未修改业务代码、原有测试、.env 或生产数据库；新增内容在 audit_artifacts 和本报告，前端构建生成 dist。没有进行浏览器点击、地图视觉、PDF/图片导出的端到端验证。

## 本轮测试结果

| 检查 | 结果与口径 |
|---|---|
| 前端 npm run build | vue-tsc 和 Vite 均通过；主包约 2.19 MB，有大包警告 |
| 后端直接 pytest | 当前 llm 环境原本无 pytest；在 audit_artifacts/python_deps 安装独立测试依赖后，收集出现 4 个 tests.fakes 导入错误（与第三方 tests 包冲突） |
| 隔离导入/临时目录后的原有测试 | 78 passed、1 failed、7 errors；API 测试继承真实 .env 的 AMAP_API_KEY，启动真实 MCP，受沙箱 uv 目录限制失败 |
| 再仅在测试进程中清空 AMAP_API_KEY | **85 passed、1 failed**；未改 .env。失败为 legacy 分支测试：仅因 hello_agents 可导入就期望 HTTP 200，实际返回 500 |
| 独立确定性行为探针 | 复现质量评估误放行、澄清状态丢失、评估失败不重规划、事件非实时、并发事件串线 |
| 本轮真实生产装配请求 | **69.81 秒，EXECUTION_FAILED，graph v2**；不是模型替身测试 |

测试环境使用 D:/Miniconda3/envs/llm/python.exe。run_suite.py 仅为本轮测试隔离第三方 tests 命名空间，并将 workdir fixture 指向可写目录；没有修改测试断言。上述 85/1 是经过这些环境隔离后的结果，不能写成原项目“开箱全绿”。

## 主要阻塞问题

### 1. P1：构建器接受无法分配执行器的组合能力任务，真实规划失败

真实请求：“2026-10-01 北京一日游，上午故宫，周边步行可达两点，地铁，预算 500 元，用地图查询真实地点。”

模型产生 context_research，其 required_capabilities 为 place_research＋weather_research。PlanBuilder 只检查各能力是否存在于全局集合（app/planning/builder.py:30），而 ExecutorRegistry 要求一个执行器覆盖整个集合（app/runtime/registry.py 的 resolve）。生产 bootstrap 把天气与高德分为不同执行器，因此出现：

`no executor supports capabilities: ['place_research', 'weather_research']`

walkable_places_research 已成功；局部修复发布 v2，保留成功任务，但坏任务的能力及依赖完全未变，再次发生同一错误，最终 synthesis 被跳过。应在构图阶段校验能力组合可执行性，并允许修复时拆分/更换任务结构，不能仅追加文字提示。

### 2. P1：Evaluation FAIL 没有进入 Partial Replanning

app/application/travel_processor.py:393 在评估 fail 时直接返回 EVALUATION_FAILED。前面的重规划循环只处理 TaskResult 执行失败，发生在合成及评估之前。

探针实际得到 EVALUATION_FAILED、repair_calls=0、replan_events=[]。这与原目标“最终行程不满足需求 → EvaluationIssues → affected_task_ids → 修复 → 再评估”不同。评估器产生的问题也没有实质填充任务定位，不能直接驱动选择性修复。

当前 ReplanningService 克隆全图，仅给目标任务 goal 追加提示，版本号递增，不改变能力或依赖。因此应称为“执行失败后的选择性重试”，尚不具备完整的问题驱动重规划能力。

### 3. P1：评估器不能可靠判断是否满足用户意图

以用户要求北京、2026-10-01 一日游的合法 DTO 为基础，以下修改均被 DeterministicTripEvaluator 判为 pass、issues=[]：

- city 改为上海。
- 整份行程改到 2027-01-01。
- days[0].date 改为 2030-12-31，但总起止日期仍为原日期。
- 要求必须去长城，实际景点没有长城，仅 assumptions 写“没有安排长城”。

原因：日期检查仅核对天数/重复日期，未比对用户日期与每日范围；must_have 使用字符串包含判断，连否定的假设文字也算满足。见 app/evaluation/deterministic.py:130、183。

另一个直接评估器探针：不存在的 evidence_id 非空就被视为有证据。该探针针对 evaluator 本身；生产 synthesizer 会剔除不存在的引用，因此不能据此断言所有伪造 ID 都能穿过合成器。但 evaluator 未核查证据内容是否支持具体声明，仍是质量验证缺口。

### 4. P1：多轮澄清没有保存/恢复意图状态

app/application/travel_processor.py:66 调用 IntentService.handle_message 时不传入 ConversationState，也丢弃返回的 conversation。

实测同一 run：第一轮“北京旅行”询问 travel_time；第二轮“三天”又询问 destination；第二轮 parser 收到的 state 为 None。前端虽有“继续”按钮，服务端不能延续前文。应按 run/session 保存 ConversationState，并供后续澄清和纠正使用。

### 5. P1：共享 Runtime 的并发请求会串事件

GraphRuntime 将 _active_event_callback 和 _active_tracker 保存在共享实例字段（app/runtime/graph_runtime.py:64、85），bootstrap 又复用一个 Runtime。两个 run 重叠时字段被后来的 run 覆盖。

用事件屏障控制 A/B 并发：A 的成功事件列表为空；B 的成功事件列表出现 A，B 自己的成功事件丢失。不是计时猜测，探针按指定顺序释放两个真实 Runtime 调用。预算 tracker 同样共享，存在跨请求归因风险，但本轮直接复现的是事件串线。应把每次运行的上下文显式传入或按异步任务隔离。

### 6. P2：页面轮询不到任务级实时进度

runtime callback 只向局部列表追加，application 在 await 整轮处理结束后才统一落库（travel_processor.py:246；travel_service.py:174 之后）。

探针在 ReAct 研究已开始并阻塞时查询持久化事件，仅有 run_created、message_received；完成后才出现 3 条 task_started。页面轮询接口是真实的，但看到的任务事件并不实时；事件 timestamp 也是最终追加时间。/events 目前返回 JSON 列表，不是 SSE。应随任务事件发生即持久化，再提供轮询或 SSE。

### 7. P1/P2：底层恢复能力没有接入产品生命周期

RecoveryService 与 GraphRuntime.resume 存在，原有测试能验证手工构造状态后的恢复规则；但 bootstrap、处理器和 runs API 没有恢复 checkpoint 的调用路径。每条新消息重新规划，首次 runtime.run 的 initial_state=None。没有产品侧中断后继续入口，也没有后台重启恢复编排。

SQLiteStateStore 以 session_id 为主键覆盖最新快照，v2 会覆盖 v1；没有完整图版本历史。Home 页面同一次加载复用 sessionId，重复创建 run 也可能复用同一个 checkpoint 标识。应区分 run、session、graph version，并接入恢复及版本快照查询。

## 已实现、应保留的部分

- Home.vue → /api/travel/runs/messages → TravelApplicationService → TravelPipelineProcessor，默认生产入口确实使用新 Runtime；当前配置 legacy=false。
- 生产 bootstrap 默认为真实 LLMIntentParser、复杂度分析、LLMStructuredPlanner、ReAct 和真实工具；Fake 通过测试注入，没有默认装配为产品模型。
- ReAct 向模型传工具 schema、回填 observation，并将上游 dependency_results 放入模型消息；不只是 metadata 占位。
- Runtime 有独立任务 gather 并发、每任务完成保存 checkpoint、依赖失败跳过下游、有限重试。
- TripPlan 合成与研究分层，并对坐标池和引用 ID 做校验；未知字段允许为空。
- 执行失败路径可发布新版本并复用未受影响的成功结果；本轮真实运行也出现这一行为。

以上是实现与局部测试证据，不代表多用户并发、进程崩溃恢复和完整行程质量已通过端到端验收。SIMPLE 当前仍调用 Planner 生成任务图，与最初的“单 ReAct”不同；Phase A/B 文档允许过渡阶段这么做，但当前未按 SIMPLE 单独限制节点数。

## 真实效果与历史记录的边界

本轮 online 请求通过真实生产 bootstrap，MCP 握手和地图研究成功后因执行器匹配失败，不能归因于“没有配置 API”。开始时 uv 缓存/工具目录被沙箱限制；仅用进程环境 UV_CACHE_DIR、UV_TOOL_DIR、UV_PYTHON 指向工作区/现有 Python 后成功启动，未更改应用配置。

历史记录中确实出现过完整 trip_plan；最近两份 smoke_20260906T031033313717Z、smoke_20260906T043336403032Z 的 evaluation 都是 indeterminate/BUDGET_UNVERIFIED，而非 pass。前端 Result.vue 没有展示 evaluation/assumptions 的专门状态，Home 会直接提示生成成功。应明确展示“预算未验证”等限制，避免把有条件草案呈现为满足要求的方案。

evals/run_online_smoke.py 不含用例成功断言，末尾无条件 exit(0)；即使用例 failed，脚本也可以退出成功。因此不能以冒烟命令退出码作为验收依据。历史记录跨多次修改、非统一基准，不计算总体成功率。

## 建议修复顺序与完成标准

1. 修复能力组合可执行性与结构修复，重跑当前真实北京案例。
2. 接通 EvaluationIssue → 修复范围 → 再合成/再评估，加入错误城市、日期、must_have 否定表达等负例。
3. 保存澄清会话，隔离 Runtime 的每请求回调和预算。
4. 接通即时事件、产品恢复及图版本历史，测试真实中断后成功任务不重跑。
5. 前端显示质量状态、证据与未知项；完成地图、导出、多轮用户操作端到端验证。
6. 修复测试命名空间与 .env 隔离，使默认离线测试无需环境补丁；给 online runner 增加明确断言和失败退出码。

不能用“文件和模块齐全”或“离线大部分通过”替代这些完成标准。

## 可复现材料

- ../backend/audit_artifacts/behavior_probes.py：确定性行为探针，模型部分使用测试替身。
- ../backend/audit_artifacts/behavior_probes.json：上述探针实际输出。
- ../backend/audit_artifacts/live_probe.py：真实生产装配请求，独立数据库，240 秒外部上限。
- ../backend/audit_artifacts/live_probe.json：本轮真实结果、错误与完整事件。
- ../backend/audit_artifacts/run_suite.py：不改业务源码的测试环境隔离器。

本报告保留了失败结果；本轮没有实施业务修复。
