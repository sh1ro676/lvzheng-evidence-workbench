import { useState } from 'react'
import { BookMarked, Search } from 'lucide-react'
import { searchTextbook } from '@/lib/api'
import type { Evidence, Textbook } from '@/types/legal'
import { EvidencePanel } from './EvidencePanel'

interface LibraryViewProps {
  books?: Textbook[]
  error: string
  onCopy: (item: Evidence) => void
}

export function LibraryView({ books, error, onCopy }: LibraryViewProps) {
  const [selectedBookId, setSelectedBookId] = useState<string>()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Evidence[]>([])
  const [selected, setSelected] = useState<Evidence>()
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const book = books?.find((item) => item.book_id === selectedBookId) ?? books?.[0]

  function chooseBook(bookId: string) {
    if (bookId === book?.book_id || loading) return
    setSelectedBookId(bookId)
    setQuery('')
    setResults([])
    setSelected(undefined)
    setMessage('')
  }

  async function runSearch() {
    if (!book || !query.trim() || loading) return
    setLoading(true)
    setMessage('')
    setResults([])
    setSelected(undefined)
    try {
      const response = await searchTextbook(query.trim(), book.book_id)
      setResults(response.evidence)
      setSelected(response.evidence[0])
      if (!response.evidence.length) setMessage('本册未找到对应段落。请换个关键词，或核对页码和册次。')
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : '教材检索失败，请重试。')
    } finally {
      setLoading(false)
    }
  }

  if (!book || !books) {
    return <div className="library-empty" role="status">{error || (books ? '尚未导入教材。导入教材索引并重启后端后，目录会显示在这里。' : '正在读取教材目录…')}</div>
  }

  return (
    <div className="library-view">
      <section className="document-switcher" aria-label="选择教材">
        <div className="document-tabs">
          {books.map((item) => (
            <button
              type="button"
              className={`document-tab ${item.book_id === book.book_id ? 'is-active' : ''}`}
              key={item.book_id}
              onClick={() => chooseBook(item.book_id)}
              aria-pressed={item.book_id === book.book_id}
              disabled={loading}
            >
              {item.title}
            </button>
          ))}
        </div>
      </section>

      <section className="document-header">
        <div className="document-icon"><BookMarked size={26} /></div>
        <div className="document-heading-copy">
          <h2>{book.title}</h2>
          <p>{book.source_name !== book.title && <>{book.source_name} · </>}{book.page_count.toLocaleString()} 页 · {book.chunk_count.toLocaleString()} 个可检索段落</p>
        </div>
      </section>

      <section className="library-search-section">
        <form className="library-search" onSubmit={(event) => { event.preventDefault(); void runSearch() }}>
          <Search size={19} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={300} placeholder="输入关键词、书内 397 或 PDF 418" aria-label="检索教材" />
          <button type="submit" className="primary-button" disabled={!query.trim() || loading}>{loading ? '检索中…' : '检索教材'}</button>
        </form>
        {message && <p className="search-message" role="status">{message}</p>}
      </section>
      {results.length > 0 && <EvidencePanel evidence={results} selectedCitationId={selected?.citation_id} onSelect={setSelected} onCopy={onCopy} compact />}
    </div>
  )
}
