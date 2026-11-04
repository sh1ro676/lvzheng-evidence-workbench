export type AnswerStatus =
  | 'verified'
  | 'retrieval_only'
  | 'insufficient_evidence'
  | 'verification_failed'
  | 'model_unavailable'
  | 'error'

export interface Evidence {
  citation_id: number
  document_id: string
  article_id: string
  document_title: string
  article_label: string
  section_path: string[]
  text: string
  content_hash: string
  source_name: string
  source_url: string
  snapshot_sha256: string
  published_at: string
  effective_at: string
  status: string
  source_notice: string
  match_type: 'exact' | 'hybrid'
  relevance: number
}

export interface VerificationCheck {
  name: string
  passed: boolean
  detail: string
}

export interface Verification {
  passed: boolean
  checks: VerificationCheck[]
}

export interface ChatResponse {
  status: AnswerStatus
  answer: string
  evidence: Evidence[]
  verification: Verification
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status?: AnswerStatus
  evidence?: Evidence[]
  verification?: Verification
}

export interface LegalDocument {
  document_id: string
  title: string
  category: string
  issuing_authority: string
  published_at: string
  effective_at: string
  status: string
  source_name: string
  source_url: string
  snapshot_sha256: string
  article_count: number
  source_notice: string
}

export interface Health {
  ok: boolean
  model_configured: boolean
  model: string
  document_count: number
  article_count: number
}
