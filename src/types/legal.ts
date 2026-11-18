export type AnswerStatus =
  | 'verified'
  | 'retrieval_only'
  | 'insufficient_evidence'
  | 'verification_failed'
  | 'model_unavailable'
  | 'source_unavailable'
  | 'error'

export type MatchType = 'exact' | 'hybrid' | 'keyword' | 'semantic' | 'web' | 'textbook'

export type EvidenceTier = 'primary' | 'reliable_report' | 'secondary_unverified'

export interface Evidence {
  citation_id: number
  document_id: string
  article_id: string
  document_title: string
  article_label: string
  section_path: string[]
  text: string
  content_hash: string
  provider?: 'pkulaw' | 'deepseek_web' | 'textbook'
  evidence_tier?: EvidenceTier
  evidence_tier_label?: string
  attribution_scope?: string
  source_name: string
  source_url: string
  snapshot_sha256: string
  published_at: string
  effective_at: string
  status: string
  effect_level?: string
  source_notice: string
  match_type: MatchType
  relevance: number
  queried_at?: string
  web_query?: string
  relevance_basis?: 'article_text' | 'topic_paper' | 'topic_related' | 'case_topic_and_opinion_text'
  identity_sources?: {
    name: string
    institution: string
    source_url: string
    document_title: string
    excerpt: string
    content_hash: string
  }[]
  institution?: string
  entity?: string
  entities?: string[]
  retrieval_round?: 'first' | 'second' | 'third'
  query_source?: 'case_base_query' | 'first_round_entity' | 'identity_lookup' | 'topic_primary' | 'topic_variant'
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

export interface WebSearchEvent {
  stage: 'plan' | 'search' | 'candidate' | 'retry' | 'fetch' | 'filter' | 'evidence' | 'entity' | 'second_round'
  ok: boolean
  query?: string
  url?: string
  title?: string
  result_count?: number
  error?: string
  reason?: string
  institution?: string
  entity?: string
  round?: 'first' | 'second' | 'third'
  query_source?: string
}

export interface ResearchLead {
  title: string
  source_url: string
  excerpt: string
  search_institution: string
}

export interface WebSearchInfo {
  enabled: boolean
  provider: 'deepseek_web'
  configured: boolean
  queries: string[]
  search_count: number
  candidate_count?: number
  fetch_attempt_count?: number
  content_fetched_count?: number
  rejected_count?: number
  fetched_count: number
  events: WebSearchEvent[]
  research_leads?: ResearchLead[]
  queried_at: string
  mode?: 'four_institutions' | 'general'
  fallback_general?: boolean
  institution_results?: {
    institution: string
    found: boolean
    search_count?: number
    candidate_count?: number
    content_fetched_count?: number
    rejected_count?: number
    entities?: string[]
    second_round?: boolean
    second_round_entity?: string
    identity_source_count?: number
    stop_reason?: string
    reason?: string
  }[]
  fallback_query?: boolean
  notice?: string
}

export interface RetrievalInfo {
  source: 'local_official' | 'pkulaw' | 'none'
  provider: 'flk' | 'pkulaw' | null
  strategy?: 'fatiao' | 'law_keyword' | 'law_semantic'
  tool?: string
  cached?: boolean
  queried_at?: string
  notice?: string
  error?: string
  web_search?: WebSearchInfo
}

export interface ChatResponse {
  status: AnswerStatus
  answer: string
  evidence: Evidence[]
  verification: Verification
  retrieval?: RetrievalInfo
  /** 是否还需要后台补充四校观点；案件不进入二次重写。 */
  followup?: 'schools' | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status?: AnswerStatus
  evidence?: Evidence[]
  verification?: Verification
  retrieval?: RetrievalInfo
  /** 第二段（四校观点）还在后台检索中 */
  following?: boolean
  /** 四校观点这一段的说明，例如未命中的原因 */
  schoolNotice?: string
  schoolRetrieval?: WebSearchInfo
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface SchoolResponse {
  status: AnswerStatus
  section: string
  /** 四校观点 / 案件讲解 */
  section_title?: string
  /** true 表示用这一节整体替换第一段的回答（案件类重写） */
  replace?: boolean
  verification?: Verification | null
  evidence: Evidence[]
  retrieval?: { web_search?: WebSearchInfo }
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

export interface ProviderStatus {
  provider: 'pkulaw'
  name: string
  configured: boolean
  capabilities: string[]
  strategy: string
  cache_ttl_seconds: number
}

export interface Health {
  ok: boolean
  model_configured: boolean
  model: string
  document_count: number
  article_count: number
  pkulaw_configured: boolean
  web_search_configured: boolean
}
