<template>
  <div class="home-container">
    <!-- 背景装饰 -->
    <div class="bg-decoration">
      <div class="circle circle-1"></div>
      <div class="circle circle-2"></div>
      <div class="circle circle-3"></div>
    </div>

    <!-- 页面标题 -->
    <div class="page-header">
      <div class="icon-wrapper">
        <span class="icon">✈️</span>
      </div>
      <h1 class="page-title">智能旅行助手</h1>
      <p class="page-subtitle">基于AI的个性化旅行规划,让每一次出行都完美无忧</p>
    </div>

    <a-card class="form-card" :bordered="false">
      <a-form
        :model="formData"
        layout="vertical"
        @finish="handleSubmit"
      >
        <!-- 第一步:目的地和日期 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">📍</span>
            <span class="section-title">目的地与日期</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="8">
              <a-form-item name="city" :rules="[{ required: true, message: '请输入目的地城市' }]">
                <template #label>
                  <span class="form-label">目的地城市</span>
                </template>
                <a-input
                  v-model:value="formData.city"
                  placeholder="例如: 北京"
                  size="large"
                  class="custom-input"
                >
                  <template #prefix>
                    <span style="color: #1890ff;">🏙️</span>
                  </template>
                </a-input>
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="start_date" :rules="[{ required: true, message: '请选择开始日期' }]">
                <template #label>
                  <span class="form-label">开始日期</span>
                </template>
                <a-date-picker
                  v-model:value="formData.start_date"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="end_date" :rules="[{ required: true, message: '请选择结束日期' }]">
                <template #label>
                  <span class="form-label">结束日期</span>
                </template>
                <a-date-picker
                  v-model:value="formData.end_date"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :span="4">
              <a-form-item>
                <template #label>
                  <span class="form-label">旅行天数</span>
                </template>
                <div class="days-display-compact">
                  <span class="days-value">{{ formData.travel_days }}</span>
                  <span class="days-unit">天</span>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第二步:偏好设置 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">⚙️</span>
            <span class="section-title">偏好设置</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="8">
              <a-form-item name="transportation">
                <template #label>
                  <span class="form-label">交通方式</span>
                </template>
                <a-select v-model:value="formData.transportation" size="large" class="custom-select">
                  <a-select-option value="公共交通">🚇 公共交通</a-select-option>
                  <a-select-option value="自驾">🚗 自驾</a-select-option>
                  <a-select-option value="步行">🚶 步行</a-select-option>
                  <a-select-option value="混合">🔀 混合</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item name="accommodation">
                <template #label>
                  <span class="form-label">住宿偏好</span>
                </template>
                <a-select v-model:value="formData.accommodation" size="large" class="custom-select">
                  <a-select-option value="经济型酒店">💰 经济型酒店</a-select-option>
                  <a-select-option value="舒适型酒店">🏨 舒适型酒店</a-select-option>
                  <a-select-option value="豪华酒店">⭐ 豪华酒店</a-select-option>
                  <a-select-option value="民宿">🏡 民宿</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item name="preferences">
                <template #label>
                  <span class="form-label">旅行偏好</span>
                </template>
                <div class="preference-tags">
                  <a-checkbox-group v-model:value="formData.preferences" class="custom-checkbox-group">
                    <a-checkbox value="历史文化" class="preference-tag">🏛️ 历史文化</a-checkbox>
                    <a-checkbox value="自然风光" class="preference-tag">🏞️ 自然风光</a-checkbox>
                    <a-checkbox value="美食" class="preference-tag">🍜 美食</a-checkbox>
                    <a-checkbox value="购物" class="preference-tag">🛍️ 购物</a-checkbox>
                    <a-checkbox value="艺术" class="preference-tag">🎨 艺术</a-checkbox>
                    <a-checkbox value="休闲" class="preference-tag">☕ 休闲</a-checkbox>
                  </a-checkbox-group>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第三步:额外要求 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">💬</span>
            <span class="section-title">额外要求</span>
          </div>

          <a-form-item name="free_text_input">
            <a-textarea
              v-model:value="formData.free_text_input"
              placeholder="请输入您的额外要求,例如:想去看升旗、需要无障碍设施、对海鲜过敏等..."
              :rows="3"
              size="large"
              class="custom-textarea"
            />
          </a-form-item>
        </div>

        <!-- 提交按钮 -->
        <a-form-item>
          <a-button
            type="primary"
            html-type="submit"
            :loading="loading"
            size="large"
            block
            class="submit-button"
          >
            <template v-if="!loading">
              <span class="button-icon">🚀</span>
              <span>开始规划我的旅行</span>
            </template>
            <template v-else>
              <span>正在生成中...</span>
            </template>
          </a-button>
        </a-form-item>

        <!-- 真实处理状态（轮询 /api/travel/runs 事件，非模拟进度） -->
        <a-form-item v-if="loading">
          <div class="loading-container">
            <a-spin size="large" />
            <p class="loading-status">{{ liveStatusText || '正在处理你的请求…' }}</p>
            <p v-if="elapsedSeconds > 0" class="loading-elapsed">已用时 {{ elapsedSeconds }} 秒（真实研究任务可能需要 1-2 分钟）</p>
          </div>
        </a-form-item>
        <AgentTracePanel v-if="agentTrace" :trace="agentTrace" />
      </a-form>

      <!-- 内联结果面板：澄清问题 / 直接答案 / 失败原因（不做假跳转） -->
      <div v-if="outcomeView !== 'none'" class="outcome-panel">
        <template v-if="outcomeView === 'clarification'">
          <a-alert type="info" show-icon :message="'需要补充信息'" :description="clarifyMessage" style="margin-bottom: 12px" />
          <div v-for="(q, qi) in clarifyQuestions" :key="`q-${qi}`" class="clarify-item">
            <span class="clarify-q">{{ qi + 1 }}. {{ q.question }}</span>
            <span v-if="q.options && q.options.length" class="clarify-options">（可参考：{{ q.options.join(' / ') }}）</span>
          </div>
          <div class="clarify-input-row">
            <a-input v-model:value="clarifyAnswer" placeholder="输入你的回答后继续，例如：北京，3 天，10月1日出发" style="flex: 1" @press-enter="sendClarifyAnswer" />
            <a-button type="primary" :loading="clarifySending" @click="sendClarifyAnswer">继续</a-button>
          </div>
        </template>

        <template v-else-if="outcomeView === 'answer'">
          <a-alert type="success" show-icon :message="'回答'" :description="answerText" />
        </template>

        <template v-else-if="outcomeView === 'error'">
          <a-alert type="error" show-icon :message="'处理失败'">
            <template #description>
              <div class="error-block">
                <p>{{ outcomeMessage }}</p>
                <p v-if="errorCode" class="error-code">错误码：{{ errorCode }}</p>
                <p class="error-hint">失败不会给你一份伪造的行程。请修改需求后重试。</p>
              </div>
            </template>
          </a-alert>
          <a-button style="margin-top: 12px" @click="resetOutcome">返回修改</a-button>
        </template>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { createTravelRun, submitTravelMessage, streamTravelEvents, getTravelRun, getTravelTrace } from '@/services/api'
import type { RunRecord, AgentTrace } from '@/services/api'
import AgentTracePanel from '@/components/AgentTracePanel.vue'
import type { TripFormData } from '@/types'
import type { Dayjs } from 'dayjs'

const router = useRouter()
const loading = ref(false)
const liveStatusText = ref('')
const elapsedSeconds = ref(0)
const currentRunId = ref<string | null>(null)
const agentTrace = ref<AgentTrace | null>(null)
const sessionId = `web-${Date.now().toString(36)}`

const outcomeView = ref<'none' | 'clarification' | 'answer' | 'error'>('none')
const outcomeMessage = ref('')
const errorCode = ref('')
const answerText = ref('')
const clarifyQuestions = ref<Array<{ question: string; options?: string[]; field?: string }>>([])
const clarifyMessage = ref('')
const clarifyAnswer = ref('')
const clarifySending = ref(false)

let pollTimer: number | null = null
let clockTimer: number | null = null
let lastEventSeq = 0

type HomeFormData = Omit<TripFormData, 'start_date' | 'end_date'> & {
  start_date: Dayjs | null
  end_date: Dayjs | null
}

const formData = reactive<HomeFormData>({
  city: '',
  start_date: null,
  end_date: null,
  travel_days: 1,
  transportation: '公共交通',
  accommodation: '经济型酒店',
  preferences: [],
  free_text_input: ''
})

// 监听日期变化,自动计算旅行天数
watch([() => formData.start_date, () => formData.end_date], ([start, end]) => {
  if (start && end) {
    const days = end.diff(start, 'day') + 1
    if (days > 0 && days <= 30) {
      formData.travel_days = days
    } else if (days > 30) {
      message.warning('旅行天数不能超过30天')
      formData.end_date = null
    } else {
      message.warning('结束日期不能早于开始日期')
      formData.end_date = null
    }
  }
})

const EVENT_TEXT: Record<string, string> = {
  run_created: '已创建本次规划会话',
  message_received: '已收到你的需求',
  intent_status: '正在理解需求',
  route_selected: '已判断问题难度，选定执行路径',
  plan_created: '正在生成研究任务计划',
  task_started: '研究任务进行中',
  task_completed: '研究任务完成',
  execution_completed: '研究执行完成',
  synthesis_completed: '正在合成行程',
  evaluation_completed: '正在做行程质量校验',
  stage_completed: '处理完成'
}

function buildMessage(): string {
  const parts = [
    `请为我规划一次 ${formData.city} 的旅行。`,
    `日期：${formData.start_date!.format('YYYY-MM-DD')} 至 ${formData.end_date!.format('YYYY-MM-DD')}，共 ${formData.travel_days} 天。`,
    `交通方式：${formData.transportation}。住宿偏好：${formData.accommodation}。`
  ]
  if (formData.preferences.length) {
    parts.push(`偏好：${formData.preferences.join('、')}。`)
  }
  if (formData.free_text_input) {
    parts.push(`补充要求：${formData.free_text_input}`)
  }
  return parts.join('')
}

function eventText(name: string): string {
  return EVENT_TEXT[name] || `正在执行：${name}`
}

function startClock(): void {
  elapsedSeconds.value = 0
  clockTimer = window.setInterval(() => {
    elapsedSeconds.value += 1
  }, 1000)
}

function stopTimers(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
  if (clockTimer !== null) {
    window.clearInterval(clockTimer)
    clockTimer = null
  }
}

function applyOutcome(record: RunRecord): void {
  const kind = record.kind
  if (kind === 'trip_plan' && record.data) {
    sessionStorage.setItem('tripPlan', JSON.stringify(record.data))
    if (currentRunId.value) sessionStorage.setItem('tripRunId', currentRunId.value)
    if (agentTrace.value) sessionStorage.setItem('agentTrace', JSON.stringify(agentTrace.value))
    if (record.data.evaluation?.status === 'indeterminate') {
      message.warning('行程已生成，部分条件尚未验证，请查看提示。')
    } else {
      message.success('旅行计划生成成功!')
    }
    router.push('/result')
    return
  }
  if (kind === 'clarification') {
    const questions = record.data?.questions || []
    clarifyQuestions.value = questions.map((q: any) => ({
      question: q.question || '',
      options: q.options || [],
      field: q.field || ''
    }))
    clarifyMessage.value = record.message || '需要补充一些信息'
    outcomeView.value = 'clarification'
    return
  }
  if (kind === 'answer') {
    answerText.value = record.message || ''
    outcomeView.value = 'answer'
    return
  }
  outcomeView.value = 'error'
  outcomeMessage.value = [record.message || '处理失败，未返回可展示的结果。',
    ...(record.data?.diagnostics || []).map((d: any) => `${d.tool || ''}：${d.message} ${d.action_required || ''}`)].join('\n')
  errorCode.value = record.error_code || ''
}

async function runMessage(messageText: string): Promise<void> {
  if (!currentRunId.value) return
  outcomeView.value = 'none'
  liveStatusText.value = '正在理解需求…'
  try {
    await submitTravelMessage(currentRunId.value, messageText)
    await streamTravelEvents(currentRunId.value, lastEventSeq, (event) => {
      lastEventSeq = event.seq
      liveStatusText.value = eventText(event.event)
    })
    const record = await getTravelRun(currentRunId.value)
    agentTrace.value = await getTravelTrace(currentRunId.value)
    applyOutcome(record)
  } catch (error: any) {
    outcomeView.value = 'error'
    outcomeMessage.value = error?.message || '请求失败，请稍后重试。'
    errorCode.value = ''
  }
}

const handleSubmit = async () => {
  if (!formData.start_date || !formData.end_date) {
    message.error('请选择日期')
    return
  }
  loading.value = true
  outcomeView.value = 'none'
  liveStatusText.value = '正在准备…'
  lastEventSeq = 0
  startClock()
  try {
    const created = await createTravelRun(sessionId)
    currentRunId.value = created.run_id
    agentTrace.value = null
    await runMessage(buildMessage())
  } catch (error: any) {
    outcomeView.value = 'error'
    outcomeMessage.value = error?.message || '无法创建规划任务，请稍后重试。'
    errorCode.value = ''
  } finally {
    stopTimers()
    loading.value = false
    elapsedSeconds.value = 0
    liveStatusText.value = ''
  }
}

const sendClarifyAnswer = async () => {
  const answer = clarifyAnswer.value.trim()
  if (!answer || !currentRunId.value) {
    message.warning('请先输入回答')
    return
  }
  clarifySending.value = true
  try {
    clarifyAnswer.value = ''
    outcomeView.value = 'none'
    loading.value = true
    liveStatusText.value = '正在处理你的回答…'
    await runMessage(answer)
  } finally {
    clarifySending.value = false
    loading.value = false
    liveStatusText.value = ''
  }
}

const resetOutcome = () => {
  outcomeView.value = 'none'
  outcomeMessage.value = ''
  errorCode.value = ''
  answerText.value = ''
  clarifyAnswer.value = ''
}

onUnmounted(() => stopTimers())
</script>

<style scoped>
.home-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 60px 20px;
  position: relative;
  overflow: hidden;
}

/* 背景装饰 */
.bg-decoration {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: hidden;
}

.circle {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  animation: float 20s infinite ease-in-out;
}

.circle-1 {
  width: 300px;
  height: 300px;
  top: -100px;
  left: -100px;
  animation-delay: 0s;
}

.circle-2 {
  width: 200px;
  height: 200px;
  top: 50%;
  right: -50px;
  animation-delay: 5s;
}

.circle-3 {
  width: 150px;
  height: 150px;
  bottom: -50px;
  left: 30%;
  animation-delay: 10s;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0) rotate(0deg);
  }
  50% {
    transform: translateY(-30px) rotate(180deg);
  }
}

/* 页面标题 */
.page-header {
  text-align: center;
  margin-bottom: 50px;
  animation: fadeInDown 0.8s ease-out;
  position: relative;
  z-index: 1;
}

.icon-wrapper {
  margin-bottom: 20px;
}

.icon {
  font-size: 80px;
  display: inline-block;
  animation: bounce 2s infinite;
}

@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-20px);
  }
}

.page-title {
  font-size: 56px;
  font-weight: 800;
  color: #ffffff;
  margin-bottom: 16px;
  text-shadow: 3px 3px 6px rgba(0, 0, 0, 0.3);
  letter-spacing: 2px;
}

.page-subtitle {
  font-size: 20px;
  color: rgba(255, 255, 255, 0.95);
  margin: 0;
  font-weight: 300;
}

/* 表单卡片 */
.form-card {
  max-width: 1400px;
  margin: 0 auto;
  border-radius: 24px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
  animation: fadeInUp 0.8s ease-out;
  position: relative;
  z-index: 1;
  backdrop-filter: blur(10px);
  background: rgba(255, 255, 255, 0.98) !important;
}

/* 表单分区 */
.form-section {
  margin-bottom: 32px;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 1px solid #e8e8e8;
  transition: all 0.3s ease;
}

.form-section:hover {
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.section-header {
  display: flex;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid #667eea;
}

.section-icon {
  font-size: 24px;
  margin-right: 12px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

/* 表单标签 */
.form-label {
  font-size: 15px;
  font-weight: 500;
  color: #555;
}

/* 自定义输入框 */
.custom-input :deep(.ant-input),
.custom-input :deep(.ant-picker) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-input :deep(.ant-input:hover),
.custom-input :deep(.ant-picker:hover) {
  border-color: #667eea;
}

.custom-input :deep(.ant-input:focus),
.custom-input :deep(.ant-picker-focused) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 自定义选择框 */
.custom-select :deep(.ant-select-selector) {
  border-radius: 12px !important;
  border: 2px solid #e8e8e8 !important;
  transition: all 0.3s ease;
}

.custom-select:hover :deep(.ant-select-selector) {
  border-color: #667eea !important;
}

.custom-select :deep(.ant-select-focused .ant-select-selector) {
  border-color: #667eea !important;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
}

/* 天数显示 - 紧凑版 */
.days-display-compact {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 8px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  color: white;
}

.days-display-compact .days-value {
  font-size: 24px;
  font-weight: 700;
  margin-right: 4px;
}

.days-display-compact .days-unit {
  font-size: 14px;
}

/* 偏好标签 */
.preference-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.custom-checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}

.preference-tag :deep(.ant-checkbox-wrapper) {
  margin: 0 !important;
  padding: 8px 16px;
  border: 2px solid #e8e8e8;
  border-radius: 20px;
  transition: all 0.3s ease;
  background: white;
  font-size: 14px;
}

.preference-tag :deep(.ant-checkbox-wrapper:hover) {
  border-color: #667eea;
  background: #f5f7ff;
}

.preference-tag :deep(.ant-checkbox-wrapper-checked) {
  border-color: #667eea;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

/* 自定义文本域 */
.custom-textarea :deep(.ant-input) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-textarea :deep(.ant-input:hover) {
  border-color: #667eea;
}

.custom-textarea :deep(.ant-input:focus) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 提交按钮 */
.submit-button {
  height: 56px;
  border-radius: 28px;
  font-size: 18px;
  font-weight: 600;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
  transition: all 0.3s ease;
}

.submit-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(102, 126, 234, 0.5);
}

.submit-button:active {
  transform: translateY(0);
}

.button-icon {
  margin-right: 8px;
  font-size: 20px;
}

/* 加载容器 */
.loading-container {
  text-align: center;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 2px dashed #667eea;
}

.loading-status {
  margin-top: 16px;
  color: #667eea;
  font-size: 18px;
  font-weight: 500;
}

.loading-elapsed {
  margin-top: 8px;
  color: #999;
  font-size: 13px;
}

/* 内联结果面板 */
.outcome-panel {
  margin-top: 8px;
  padding: 20px 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 1px solid #e8e8e8;
}

.clarify-item {
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #fafafa;
  border-left: 3px solid #667eea;
  border-radius: 6px;
}

.clarify-q {
  font-size: 15px;
  color: #333;
}

.clarify-options {
  display: block;
  font-size: 12px;
  color: #888;
  margin-top: 4px;
}

.clarify-input-row {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

.error-block p {
  margin: 4px 0;
}

.error-code {
  color: #cf1322;
  font-family: monospace;
}

.error-hint {
  color: #888;
  font-size: 13px;
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
