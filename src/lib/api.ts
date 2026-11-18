import type { ChatResponse, ChatTurn, Evidence, Health, Textbook, ProviderStatus, SchoolResponse } from '@/types/legal'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`)
  return data as T
}

export function getHealth() {
  return request<Health>('/api/health')
}

export function getTextbooks() {
  return request<{ books: Textbook[] }>('/api/expert-library/books')
}

export function getProviders() {
  return request<{ providers: ProviderStatus[] }>('/api/providers')
}

export function searchTextbook(query: string, bookId: string) {
  const params = new URLSearchParams({ q: query, book_id: bookId })
  return request<{ evidence: Evidence[] }>(`/api/expert-library/search?${params}`)
}

export function askLaw(question: string, webSearch = true, documentId?: string, history: ChatTurn[] = []) {
  return request<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ question, document_id: documentId, web_search: webSearch, history }),
  })
}

/** 第二段：后台补四校观点。第一段（法条＋教材）已经先渲染，这里只追加。 */
export async function askSchools(question: string, timeoutMs = 180000, history: ChatTurn[] = [], signal?: AbortSignal) {
  // 第二段要跑全网 + 四校，正常 20–60 秒；超过三分钟仍未回来说明卡住了，
  // 主动中断，避免页面永远停在"正在检索"。
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await request<SchoolResponse>('/api/schools', {
      method: 'POST',
      body: JSON.stringify({ question, history }),
      signal: signal ? AbortSignal.any([signal, controller.signal]) : controller.signal,
    })
  } finally {
    clearTimeout(timer)
  }
}

export type AgentStage = 'planning' | 'retrieving' | 'supplementing' | 'generating' | 'verifying'

/** 主问答走流式：真实阶段、正文增量和最终结果分别回调。 */
export async function askLawStream(
  question: string,
  onDelta: (text: string) => void,
  onReplace?: (text: string) => void,
  webSearch = true,
  onStage?: (stage: AgentStage) => void,
  history: ChatTurn[] = [],
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, web_search: webSearch, history }),
    signal,
  })
  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => ({}))
    throw new Error((detail as { error?: string }).error || `请求失败（${response.status}）`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let final: ChatResponse | null = null

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        let payload: Record<string, unknown>
        try {
          payload = JSON.parse(line.slice(5).trim())
        } catch {
          continue
        }
        if (typeof payload.stage === 'string') onStage?.(payload.stage as AgentStage)
        if (typeof payload.delta === 'string') onDelta(payload.delta)
        // 校验没过时后端会重生成一次，用 replace 事件覆盖已渲染的正文。
        if (payload.replace && typeof payload.answer === 'string') onReplace?.(payload.answer)
        if (payload.error) throw new Error(String(payload.error))
        if (payload.done) final = payload as unknown as ChatResponse
      }
    }
    if (!final) throw new Error('流式响应中断')
    return final
  } finally {
    reader.releaseLock()
  }
}
