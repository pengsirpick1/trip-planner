<template>
  <a-card class="agent-trace" :bordered="false" title="🤖 Agent 执行轨迹">
    <p class="intro">展示任务规划、工具研究和质量校验的可验证记录，不展示未验证的模型内部推理。</p>
    <a-steps size="small" :current="step">
      <a-step title="理解需求" /><a-step title="规划任务图" /><a-step title="工具研究" /><a-step title="合成校验" />
    </a-steps>
    <div v-if="trace?.route" class="route">执行路径：<b>{{ trace.route.route || '动态路由' }}</b><span v-if="trace.route.complexity_score != null"> · 复杂度 {{ trace.route.complexity_score }}</span></div>
    <div v-if="executionProfile" class="execution-profile">
      <b>执行档位：{{ profileLabel(executionProfile) }}</b>
      <span v-if="researchTaskBudget != null"> · 研究任务预算 {{ researchTaskBudget }} 条</span>
      <span v-if="workerBudget != null"> · 本会话最多占用 {{ workerBudget }} 个本地 Worker</span>
      <span v-if="researchTasks.length"> · 本图 {{ researchTasks.length }} 条独立研究任务</span>
      <span v-if="trace?.graph?.graph_compiler"> · 依赖由 Graph Compiler 编译</span>
    </div>
    <div v-if="trace?.graph?.pruned_task_ids?.length" class="compiler-note">
      已裁剪无效研究节点：{{ trace.graph.pruned_task_ids.join('、') }}
    </div>
    <div v-if="trace?.graph?.tool_pool" class="pool-note">
      工具池：高德 MCP {{ trace.graph.tool_pool.pool_size }} 个连接，全局限流 {{ trace.graph.tool_pool.max_qps }} QPS
    </div>
    <div v-if="tasks.length" class="stats"><span>{{ tasks.length }} 个任务</span><span>{{ completed }} 个已完成</span><span>{{ tools }} 次工具调用</span></div>
    <div v-if="tasks.length" class="dag-wrap">
      <div class="dag-caption">任务按依赖层级从左到右执行；箭头表示下游任务只会读取已完成的上游研究结果。</div>
      <div class="dag-board">
        <section v-for="(layer, layerIndex) in layers" :key="layerIndex" class="dag-layer">
          <div class="layer-title">{{ layerIndex === 0 ? '独立研究' : `依赖层 ${layerIndex}` }}</div>
          <div v-for="task in layer" :key="task.id" class="task dag-node" :class="task.status">
        <div class="task-head"><b>{{ task.id }}</b><a-tag :color="statusColor(task.status)">{{ statusText(task.status) }}</a-tag></div>
        <p>{{ task.goal }}</p>
        <div class="chips"><a-tag v-for="cap in task.capabilities" :key="cap">{{ cap }}</a-tag></div>
        <div v-if="task.dependencies.length" class="dependency">← 输入：{{ task.dependencies.join('、') }}</div>
        <div v-if="task.assigned_agent" class="muted">Sub-Agent：{{ task.assigned_agent }}</div>
        <div v-if="task.executor_id" class="muted">执行器：{{ task.executor_id }} · {{ task.tool_call_count }} 次调用</div>
        <div v-if="task.metrics" class="metrics">
          总耗时 {{ seconds(task.metrics.total_time_ms) }}s · LLM {{ seconds(task.metrics.llm_time_ms) }}s（{{ task.metrics.llm_calls }} 次） · 工具等待 {{ seconds(task.metrics.tool_wait_time_ms) }}s
          <span v-if="task.metrics.retry_time_ms"> · 重试 {{ seconds(task.metrics.retry_time_ms) }}s</span>
          <br />Token：输入 {{ task.metrics.input_tokens }} / 输出 {{ task.metrics.output_tokens }}
        </div>
        <a-collapse v-if="task.tool_summary.length" ghost>
          <a-collapse-panel key="tools" :header="`查看 ${task.tool_summary.length} 条工具记录`">
            <div v-for="(tool, i) in task.tool_summary" :key="i" class="tool"><span>{{ tool.success ? '✓' : '!' }} {{ tool.tool_name }}</span><span>{{ tool.source || '' }} {{ tool.latency_ms ? ` ${tool.latency_ms}ms` : '' }}</span><small v-if="tool.error">{{ tool.error }}</small></div>
          </a-collapse-panel>
        </a-collapse>
        <small v-if="task.error" class="error">{{ task.error }}</small>
          </div>
        </section>
      </div>
    </div>
    <a-empty v-else description="本轮未生成任务图（可能处于澄清需求阶段）" :image-style="{ height: '42px' }" />
    <a-collapse v-if="trace?.timeline?.length" ghost>
      <a-collapse-panel key="events" header="查看执行事件">
        <a-timeline><a-timeline-item v-for="event in visibleEvents" :key="event.seq">{{ eventLabel(event.event) }} <small>{{ formatTime(event.timestamp) }}</small></a-timeline-item></a-timeline>
      </a-collapse-panel>
    </a-collapse>
  </a-card>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { AgentTrace } from '@/services/api'
const props = defineProps<{ trace?: AgentTrace | null }>()
const tasks = computed(() => props.trace?.graph?.tasks || [])
const completed = computed(() => tasks.value.filter(t => t.status === 'succeeded').length)
const tools = computed(() => tasks.value.reduce((sum, t) => sum + t.tool_call_count, 0))
const executionProfile = computed(() => props.trace?.graph?.execution_profile || props.trace?.route?.execution_profile)
const maxResearchAgents = computed(() => props.trace?.graph?.max_research_agents ?? props.trace?.route?.max_research_agents)
const researchTaskBudget = computed(() => props.trace?.graph?.research_task_budget ?? props.trace?.route?.research_task_budget ?? maxResearchAgents.value)
const workerBudget = computed(() => props.trace?.graph?.worker_concurrency_budget ?? props.trace?.route?.worker_concurrency_budget)
// 只统计没有输入依赖、且实际由 Research Agent 执行的节点。预算策略和最终合成不计入。
const researchTasks = computed(() => tasks.value.filter(task =>
  task.dependencies.length === 0 && task.executor_id !== 'budget_policy' && !/synthesis|budget_policy/i.test(task.id)
))
const layers = computed(() => {
  const remaining = new Map(tasks.value.map(task => [task.id, task]))
  const resolved = new Set<string>()
  const result: typeof tasks.value[] = []
  while (remaining.size) {
    const layer = [...remaining.values()].filter(task => task.dependencies.every(dep => resolved.has(dep) || !remaining.has(dep)))
    // 数据异常时也要展示节点，避免循环依赖导致界面空白。
    const current = layer.length ? layer : [...remaining.values()]
    result.push(current)
    current.forEach(task => { resolved.add(task.id); remaining.delete(task.id) })
  }
  return result
})
const step = computed(() => !props.trace?.route ? 0 : !tasks.value.length ? 1 : completed.value < tasks.value.length ? 2 : 3)
const visibleEvents = computed(() => (props.trace?.timeline || []).slice(-12))
const labels: Record<string, string> = { route_selected:'已选择执行路径', plan_created:'已创建动态任务图', agent_revision_requests:'已向相关 Sub-Agent 发出局部修订请求', plan_revision_requested:'已识别为行程修订', task_ready:'任务已就绪', task_started:'任务进入调度队列', executor_selected:'已选择执行器', worker_assigned:'本地 Worker 已领取任务', task_completed:'任务完成', task_succeeded:'任务完成', synthesis_completed:'已合成行程', evaluation_completed:'已完成质量校验', stage_completed:'本轮处理结束' }
function eventLabel(name: string) { return labels[name] || name }
function statusText(status: string) { return ({ pending:'等待', ready:'就绪', running:'执行中', succeeded:'已完成', failed:'失败', skipped:'跳过', waiting_for_user:'等待确认' } as Record<string,string>)[status] || status }
function statusColor(status: string) { return ({ succeeded:'success', running:'processing', failed:'error', pending:'default', ready:'blue', skipped:'warning', waiting_for_user:'gold' } as Record<string,string>)[status] || 'default' }
function formatTime(value: string) { return value ? new Date(value).toLocaleTimeString() : '' }
function seconds(milliseconds: number) { return (milliseconds / 1000).toFixed(2) }
function profileLabel(profile: string) {
  return ({ direct: 'DIRECT（直接答复）', light: 'LIGHT（轻量并行）', standard: 'STANDARD（标准并行）', deep: 'DEEP（深度并行）' } as Record<string, string>)[profile.toLowerCase()] || profile
}
</script>
<style scoped>
.agent-trace{margin-top:20px}.intro,.muted,.dependency,.metrics{color:#667085;font-size:13px}.metrics{margin-top:7px;padding:7px 8px;background:#f6f8fb;border-radius:6px;line-height:1.6}.route,.stats{margin:14px 0}.execution-profile,.compiler-note,.pool-note{margin:8px 0;padding:8px 10px;border-radius:7px;font-size:13px}.execution-profile{color:#1d4ed8;background:#eff6ff;border:1px solid #bfdbfe}.compiler-note{color:#166534;background:#f0fdf4;border:1px solid #bbf7d0}.pool-note{color:#5b21b6;background:#f5f3ff;border:1px solid #ddd6fe}.stats{display:flex;gap:16px;font-weight:600}.dag-wrap{margin-top:14px}.dag-caption{font-size:13px;color:#667085;margin-bottom:8px}.dag-board{display:flex;gap:28px;overflow-x:auto;padding:12px 4px 18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px}.dag-layer{min-width:270px;display:flex;flex-direction:column;gap:12px;position:relative}.dag-layer:not(:last-child)::after{content:'➜';position:absolute;right:-23px;top:47%;color:#1677ff;font-size:22px;font-weight:bold}.layer-title{font-size:12px;font-weight:700;color:#475569;text-align:center}.task{border:1px solid #e5e7eb;border-left:4px solid #94a3b8;border-radius:8px;padding:12px;background:#fff}.dag-node{box-shadow:0 2px 6px rgba(15,23,42,.06)}.task.running{border-left-color:#1677ff}.task.succeeded{border-left-color:#52c41a}.task.failed{border-left-color:#ff4d4f}.task-head{display:flex;justify-content:space-between;gap:8px}.task p{margin:8px 0;line-height:1.5}.chips{margin-bottom:6px}.tool{display:flex;flex-direction:column;border-bottom:1px solid #f0f0f0;padding:6px 0;font-size:12px}.error{color:#cf1322}.agent-trace :deep(.ant-card-head){background:linear-gradient(135deg,#1677ff,#722ed1);color:#fff}@media (max-width:700px){.dag-board{gap:24px}.dag-layer{min-width:240px}}
</style>
