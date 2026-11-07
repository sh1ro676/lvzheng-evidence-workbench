import type { ChatResponse, Evidence, Health, LegalDocument, ProviderStatus } from '@/types/legal'

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

export function getDocuments() {
  return request<{ documents: LegalDocument[] }>('/api/documents')
}

export function getProviders() {
  return request<{ providers: ProviderStatus[] }>('/api/providers')
}

export function searchLaw(question: string, documentId?: string) {
  const params = new URLSearchParams({ q: question })
  if (documentId) params.set('document_id', documentId)
  return request<{ evidence: Evidence[] }>(`/api/search?${params}`)
}

export function askLaw(question: string, documentId?: string) {
  return request<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ question, document_id: documentId }),
  })
}
