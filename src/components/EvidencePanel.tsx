import { BookOpen, Check, Copy, ExternalLink, FileCheck2, Fingerprint } from 'lucide-react'
import type { Evidence, MatchType } from '@/types/legal'

interface EvidencePanelProps {
  evidence: Evidence[]
  selectedId?: string
  onSelect: (item: Evidence) => void
  onCopy: (item: Evidence) => void
  compact?: boolean
}

const matchLabels: Record<MatchType, string> = {
  exact: '条号精确匹配',
  hybrid: '本地相关检索',
  keyword: '法宝关键词检索',
  semantic: '法宝语义检索',
}

export function EvidencePanel({ evidence, selectedId, onSelect, onCopy, compact = false }: EvidencePanelProps) {
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
            const selected = selectedId === item.article_id
            return (
              <article
                className={`evidence-item ${selected ? 'is-selected' : ''}`}
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
                <div className="section-path">{item.section_path.join(' / ') || (item.provider === 'pkulaw' ? '北大法宝 MCP 实时证据' : '正文')}</div>
                <div className={`legal-original ${!selected ? 'is-preview' : ''}`}>
                  {item.text.split('\n').map((paragraph, index) => <p key={index}>{paragraph}</p>)}
                </div>
                {selected && (
                  <div className="evidence-detail">
                    <dl className="source-metadata">
                      <div><dt>效力</dt><dd><span className="status-dot" />{item.status || '以来源为准'}</dd></div>
                      {item.effect_level && <div><dt>效力级别</dt><dd>{item.effect_level}</dd></div>}
                      <div><dt>公布日期</dt><dd>{item.published_at || '以来源为准'}</dd></div>
                      <div><dt>施行日期</dt><dd>{item.effective_at || '以来源为准'}</dd></div>
                      <div><dt>来源</dt><dd>{item.source_name}</dd></div>
                    </dl>
                    {item.snapshot_sha256 && <div className="hash-line" title={item.snapshot_sha256}>
                      <Fingerprint size={14} />原文快照 {item.snapshot_sha256.slice(0, 12)}…
                    </div>}
                    <div className="evidence-actions">
                      <button type="button" className="text-action" onClick={() => onCopy(item)}><Copy size={15} />复制原文</button>
                      {item.source_url && <a className="text-action" href={item.source_url} target="_blank" rel="noreferrer">{item.provider === 'pkulaw' ? '法宝原文' : '官方来源'}<ExternalLink size={14} /></a>}
                    </div>
                  </div>
                )}
              </article>
            )
          })}
          <div className="source-notice"><Check size={14} />{evidence[0].source_notice}</div>
        </div>
      )}
    </section>
  )
}
