import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// 长任务（真实规划可能数分钟）：消息接口单独放宽超时
const LONG_TIMEOUT = 600000
const ACCESS_TOKEN_KEY = 'trip_planner_access_token'
const AUTH_USER_KEY = 'trip_planner_auth_user'

export interface AuthUser { id: string; username: string }
export interface ApiErrorPayload { code: string; message: string; trace_id: string }
export class ApiError extends Error {
  constructor(public readonly payload: ApiErrorPayload, public readonly status?: number) { super(payload.message) }
}
export interface AuthResponse { access_token: string; token_type: string; user: AuthUser }
export type SupportedProvider = 'openai' | 'deepseek'
export interface ProviderCredential { provider: SupportedProvider; base_url: string; model?: string | null; is_active: boolean; key_version: number; last_verified_at?: string | null; last_error_code?: string | null; updated_at: string }

export function getAccessToken(): string | null { return sessionStorage.getItem(ACCESS_TOKEN_KEY) }
export function getAuthUser(): AuthUser | null {
  const raw = sessionStorage.getItem(AUTH_USER_KEY)
  try { return raw ? JSON.parse(raw) as AuthUser : null } catch { return null }
}
export function clearAuth(): void { sessionStorage.removeItem(ACCESS_TOKEN_KEY); sessionStorage.removeItem(AUTH_USER_KEY) }
function saveAuth(response: AuthResponse): AuthResponse {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, response.access_token)
  sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify(response.user))
  return response
}

export interface RunEventItem {
  seq: number
  event: string
  payload: Record<string, unknown>
  timestamp: string
}

export interface RunRecord {
  run_id: string
  session_id?: string | null
  status: string
  kind?: string | null
  message: string
  error_code?: string | null
  data?: any
  events?: RunEventItem[]
}

export interface AgentTrace {
  run_id: string
  status: string
  route?: {
    route?: string; complexity_score?: number; reasons?: string[]
    execution_profile?: string; max_research_agents?: number; research_task_budget?: number; worker_concurrency_budget?: number
  } | null
  graph?: {
    graph_id: string; version: number; status: string; tasks: AgentTraceTask[]
    execution_profile?: string | null; max_research_agents?: number | null; research_task_budget?: number | null; worker_concurrency_budget?: number | null
    graph_compiler?: string | null; pruned_task_ids?: string[]
    public_transit_supported?: boolean | null
    tool_pool?: { provider: string; pool_size: number; max_qps: number } | null
  } | null
  evaluation?: { status?: string; issues?: string[] } | null
  timeline: RunEventItem[]
}

export interface AgentTraceTask {
  id: string; goal: string; dependencies: string[]; capabilities: string[]; status: string
  assigned_agent?: string | null; agent_local_state?: { observation_count?: number; sources?: string[] } | null
  executor_id?: string | null; tool_call_count: number
  tool_summary: Array<{ tool_name: string; success: boolean; source?: string | null; latency_ms?: number | null; error?: string | null }>
  sources: string[]; error?: string | null
  metrics?: { llm_time_ms: number; tool_wait_time_ms: number; retry_time_ms: number; total_time_ms: number; llm_calls: number; tool_calls: number; input_tokens: number; output_tokens: number; total_tokens: number } | null
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000, // 2分钟超时（创建/查询足够）
  headers: {
    'Content-Type': 'application/json'
  }
})

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
apiClient.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) clearAuth()
    const payload = error.response?.data
    if (payload?.code && payload?.message && payload?.trace_id) return Promise.reject(new ApiError(payload, error.response.status))
    return Promise.reject(error)
  }
)

export async function register(username: string, password: string): Promise<AuthResponse> {
  return saveAuth((await apiClient.post<AuthResponse>('/api/auth/register', { username, password })).data)
}
export async function login(username: string, password: string): Promise<AuthResponse> {
  return saveAuth((await apiClient.post<AuthResponse>('/api/auth/login', { username, password })).data)
}
export async function getCurrentUser(): Promise<AuthUser> {
  return (await apiClient.get<AuthUser>('/api/auth/me')).data
}
export async function listProviderCredentials(): Promise<ProviderCredential[]> {
  return (await apiClient.get<{ items: ProviderCredential[] }>('/api/provider-credentials')).data.items
}
export async function saveProviderCredential(provider: SupportedProvider, apiKey: string, baseUrl?: string, model?: string): Promise<void> {
  await apiClient.put(`/api/provider-credentials/${provider}`, { provider, api_key: apiKey, base_url: baseUrl || null, model: model || null })
}
export async function deleteProviderCredential(provider: SupportedProvider): Promise<void> {
  await apiClient.delete(`/api/provider-credentials/${provider}`)
}
export async function activateProviderCredential(provider: SupportedProvider): Promise<void> { await apiClient.post(`/api/provider-credentials/${provider}/activate`) }
export async function verifyProviderCredential(provider: SupportedProvider): Promise<void> { await apiClient.post(`/api/provider-credentials/${provider}/verify`) }

export async function createTravelRun(sessionId?: string): Promise<RunRecord> {
  const response = await apiClient.post<RunRecord>('/api/travel/runs', { session_id: sessionId })
  return response.data
}

export async function sendTravelMessage(runId: string, message: string): Promise<RunRecord> {
  const response = await apiClient.post<RunRecord>(
    `/api/travel/runs/${runId}/messages`,
    { message },
    { timeout: LONG_TIMEOUT }
  )
  return response.data
}
export async function submitTravelMessage(runId: string, message: string): Promise<void> {
  await apiClient.post(`/api/travel/runs/${runId}/messages/async`, { message })
}
export async function streamTravelEvents(runId: string, afterSeq: number, onEvent: (event: RunEventItem) => void): Promise<void> {
  const token = getAccessToken()
  const response = await fetch(`${API_BASE_URL}/api/travel/runs/${runId}/stream?after_seq=${afterSeq}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  })
  if (!response.ok || !response.body) throw new Error('无法连接任务进度流')
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const frames = buffer.split('\n\n'); buffer = frames.pop() || ''
    for (const frame of frames) {
      const data = frame.split('\n').find(line => line.startsWith('data: '))?.slice(6)
      const event = frame.split('\n').find(line => line.startsWith('event: '))?.slice(7)
      if (data && event !== 'stream_completed') onEvent(JSON.parse(data) as RunEventItem)
    }
    if (done) return
  }
}

export async function getTravelRun(runId: string): Promise<RunRecord> {
  const response = await apiClient.get<RunRecord>(`/api/travel/runs/${runId}`)
  return response.data
}

export async function listTravelEvents(runId: string): Promise<RunEventItem[]> {
  const response = await apiClient.get<RunEventItem[]>(`/api/travel/runs/${runId}/events`)
  return response.data
}

export async function getTravelTrace(runId: string): Promise<AgentTrace> {
  const response = await apiClient.get<AgentTrace>(`/api/travel/runs/${runId}/trace`)
  return response.data
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient
