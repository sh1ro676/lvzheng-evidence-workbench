import { useState } from 'react'
import { ArrowUpRight, BookMarked, CalendarDays, Landmark, Search } from 'lucide-react'
import { searchLaw } from '@/lib/api'
import type { Evidence, LegalDocument } from '@/types/legal'
import { EvidencePanel } from './EvidencePanel'

interface LibraryViewProps {
  documents: LegalDocument[]
  onCopy: (item: Evidence) => void
}

export function LibraryView({ documents, onCopy }: LibraryViewProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Evidence[]>([])
  const [selected, setSelected] = useState<Evidence>()
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('输入条号或自然语言，直接核验本地官方文库。')
  const document = documents[0]

  async function runSearch(value: string) {
    if (!value.trim() || loading) return
    setQuery(value)
    setLoading(true)
    setMessage('')
    try {
      const response = await searchLaw(value, document?.document_id)
      setResults(response.evidence)
      setSelected(response.evidence[0])
      if (!response.evidence.length) setMessage('未找到足够相关的条文，请尝试明确条号或调整关键词。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '检索失败')
    } finally {
      setLoading(false)
    }
  }

  if (!document) return <div className="library-empty">法律文库尚未入库。</div>

  return (
    <div className="library-view">
      <section className="document-header">
        <div className="document-icon"><BookMarked size={26} /></div>
        <div className="document-heading-copy">
          <span className="section-label">OFFICIAL COLLECTION</span>
          <h1>{document.title}</h1>
          <p>{document.source_name} · {document.article_count.toLocaleString()} 条结构化原文</p>
        </div>
        <a href={document.source_url} target="_blank" rel="noreferrer" className="secondary-button">访问官方页面<ArrowUpRight size={16} /></a>
      </section>
      <section className="document-facts" aria-label="文献元数据">
        <div><Landmark size={17} /><span>制定机关</span><strong>{document.issuing_authority}</strong></div>
        <div><CalendarDays size={17} /><span>公布日期</span><strong>{document.published_at}</strong></div>
        <div><CalendarDays size={17} /><span>施行日期</span><strong>{document.effective_at}</strong></div>
        <div><span className="status-dot" /><span>效力状态</span><strong>{document.status}</strong></div>
      </section>
      <section className="library-search-section">
        <form className="library-search" onSubmit={(event) => { event.preventDefault(); void runSearch(query) }}>
          <Search size={19} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：第 1165 条，或离婚冷静期" aria-label="检索法律文库" />
          <button type="submit" className="primary-button" disabled={!query.trim() || loading}>{loading ? '检索中…' : '检索原文'}</button>
        </form>
        <div className="quick-searches">
          <span>快速检索</span>
          {['第 1 条', '第 1165 条', '离婚冷静期', '高空抛物'].map((item) => (
            <button type="button" key={item} onClick={() => void runSearch(item)}>{item}</button>
          ))}
        </div>
        {message && <p className="search-message" role="status">{message}</p>}
      </section>
      <EvidencePanel evidence={results} selectedId={selected?.article_id} onSelect={setSelected} onCopy={onCopy} compact />
    </div>
  )
}
