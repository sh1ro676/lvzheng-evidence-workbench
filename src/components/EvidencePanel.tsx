import { BookOpen, Check, Copy, ExternalLink, FileCheck2, Fingerprint, Globe, MapPin, University } from 'lucide-react'
import type { Evidence, EvidenceTier, MatchType } from '@/types/legal'

interface EvidencePanelProps {
  evidence: Evidence[]
  selectedCitationId?: number
  onSelect: (item: Evidence) => void
  onCopy: (item: Evidence) => void
  compact?: boolean
}

const matchLabels: Record<MatchType, string> = {
  exact: '条号精确匹配',
  hybrid: '本地相关检索',
  keyword: '法宝关键词检索',
  semantic: '法宝语义检索',
  web: '联网正文',
  textbook: '教材内容 · OCR 待核',
}

const evidenceTierLabels: Record<EvidenceTier, string> = {
  primary: '一手资料',
  reliable_report: '媒体报道',
  secondary_unverified: '转述待核',
}

export function EvidencePanel({ evidence, selectedCitationId, onSelect, onCopy, compact = false }: EvidencePanelProps) {
  return (
    <section className={`evidence-panel ${compact ? 'is-compact' : ''}`} aria-label="原文证据">
      <div className="panel-heading">
        <div>
          <span className="section-label">SOURCE EVIDENCE</span>
          <h2>原文证据{evidence.length > 0 && <span className="count-badge">{evidence.length}</span>}</h2>
        </div>
        {evidence.length > 0 && <span className="verified-label"><FileCheck2 size={15} />可追溯</span>}
      </div>

      {evidence.length === 0 ? (
        <div className="evidence-empty">
          <BookOpen size={28} strokeWidth={1.5} />
          <h3>原文在这里展开</h3>
          <p>提交任务后，可逐条核验法律依据、效力信息与来源。</p>
        </div>
      ) : (
        <div className="evidence-list">
          {evidence.map((item) => {
            const selected = selectedCitationId === item.citation_id
            const isTextbook = item.provider === 'textbook'
            const evidenceTierLabel = item.provider === 'deepseek_web' && item.evidence_tier
              ? (item.evidence_tier_label || evidenceTierLabels[item.evidence_tier])
              : undefined
            return (
              <article
                className={`evidence-item ${selected ? 'is-selected' : ''} ${isTextbook ? 'is-textbook' : ''}`}
                id={`evidence-${item.citation_id}`}
                key={`${item.document_id}-${item.article_id}`}
              >
                <button type="button" className="evidence-title-button" onClick={() => onSelect(item)} aria-expanded={selected}>
                  <span className="evidence-number">{String(item.citation_id).padStart(2, '0')}</span>
                  <span className="evidence-title-copy">
                    <span className="evidence-document">{item.document_title} · {item.source_name}</span>
                    <strong>{item.article_label}</strong>
                  </span>
                  <span className="match-label">{matchLabels[item.match_type]}</span>
                </button>
                {(item.institution || item.relevance_basis || evidenceTierLabel) && (
                  <div className="evidence-taxonomy">
                    {evidenceTierLabel && <span className={`evidence-tier-badge is-${item.evidence_tier}`}>{evidenceTierLabel}</span>}
                    {item.institution && <span className="evidence-institution"><University size={13} />{item.institution}</span>}
                    {item.relevance_basis === 'article_text' && <span className="relevance-badge is-direct">正文提及所问条号</span>}
                    {item.relevance_basis === 'topic_paper' && <span className="relevance-badge is-topic">主题相关论文</span>}
                    {item.relevance_basis === 'topic_related' && <span className="relevance-badge is-topic">主题相关网页</span>}
                  </div>
                )}
                {isTextbook && selected ? (
                  <div className="textbook-location" aria-label="教材原文定位">
                    <div className="textbook-location-heading"><MapPin size={15} /><strong>教材原文定位</strong><span>OCR 原文</span></div>
                    <dl>
                      <div><dt>教材名称</dt><dd>{item.document_title}</dd></div>
                      <div><dt>原文位置</dt><dd>{item.article_label}</dd></div>
                    </dl>
                    <p>以下为该页提取的对应段落，可能有个别错字；正式引用前请核对纸书。</p>
                  </div>
                ) : (
                  <div className="section-path">{item.provider === 'deepseek_web' && <Globe size={12} />}{item.section_path.join(' / ') || (item.provider === 'pkulaw' ? '北大法宝 MCP 实时证据' : '正文')}</div>
                )}
                <div className={`legal-original ${!selected ? 'is-preview' : ''}`} aria-label={isTextbook ? '教材 OCR 原文' : undefined}>
                  {item.text.split('\n').map((paragraph, index) => <p key={index}>{paragraph}</p>)}
                </div>
                {(item.relevance_basis === 'topic_paper' || item.relevance_basis === 'topic_related') && <div className="evidence-topic-warning">该资料用于主题研究，不作为所问条号的逐条释义依据。</div>}
                {selected && (
                  <div className="evidence-detail">
                    <dl className="source-metadata">
                      <div><dt>{item.provider === 'deepseek_web' ? '资料状态' : '效力'}</dt><dd><span className="status-dot" />{item.status || '以来源为准'}</dd></div>
                      {item.effect_level && <div><dt>效力级别</dt><dd>{item.effect_level}</dd></div>}
                      <div><dt>发布日期</dt><dd>{item.published_at || '以来源为准'}</dd></div>
                      {item.provider !== 'deepseek_web' && <div><dt>施行日期</dt><dd>{item.effective_at || '以来源为准'}</dd></div>}
                      <div><dt>来源</dt><dd>{item.source_name}</dd></div>
                    </dl>
                    {item.web_query && <div className="web-query">检索词：{item.web_query}</div>}
                    {item.identity_sources?.map((identity) => (
                      <div className="web-query" key={`${identity.name}-${identity.source_url}`}>
                        <strong>身份核验：{identity.name} · {identity.institution}</strong>
                        <p>{identity.excerpt}</p>
                        <a className="text-action" href={identity.source_url} target="_blank" rel="noreferrer">
                          {identity.document_title}<ExternalLink size={14} />
                        </a>
                        <p>此页仅核验任职关联，观点以本条正文为准。</p>
                      </div>
                    ))}
                    {item.snapshot_sha256 && <div className="hash-line" title={item.snapshot_sha256}>
                      <Fingerprint size={14} />{item.provider === 'deepseek_web' ? '正文 SHA-256' : '原文快照'} {item.snapshot_sha256.slice(0, 12)}…
                    </div>}
                    <div className="evidence-actions">
                      <button type="button" className="text-action" onClick={() => onCopy(item)}><Copy size={15} />复制原文</button>
                      {item.source_url && <a className="text-action" href={item.source_url} target="_blank" rel="noreferrer">{item.provider === 'pkulaw' ? '法宝原文' : item.provider === 'deepseek_web' ? '打开网页' : '官方来源'}<ExternalLink size={14} /></a>}
                    </div>
                  </div>
                )}
              </article>
            )
          })}
          {[...new Set(evidence.map((item) => item.source_notice))].map((notice) => <div className="source-notice" key={notice}><Check size={14} />{notice}</div>)}
        </div>
      )}
    </section>
  )
}
