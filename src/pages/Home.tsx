import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle, ArrowUp, BookOpen, CheckCircle2, ChevronRight, CircleHelp,
  FileSearch, Library, LoaderCircle, Plus, Scale, ShieldCheck, Sparkles,
} from 'lucide-react'
import { AnswerText } from '@/components/AnswerText'
import { EvidencePanel } from '@/components/EvidencePanel'
import { LibraryView } from '@/components/LibraryView'
import { ModelDialog } from '@/components/ModelDialog'
import { askLaw, getDocuments, getHealth, getProviders } from '@/lib/api'
import type { AnswerStatus, ChatMessage, Evidence, Health, LegalDocument, ProviderStatus } from '@/types/legal'
import '../App.css'

const examples = [
  { title: '精确读取', query: '请给出《中华人民共和国民法典》第 1165 条原文，并解释其含义。' },
  { title: '对比分析', query: '对比第一千一百六十五条与第一千一百六十六条的归责原则。' },
  { title: '通俗改写', query: '把第一千零七十七条改写成一段面向当事人的通俗说明，保留关键期限。' },
]

const statusLabels: Record<AnswerStatus, string> = {
  verified: '引用校验通过',
  retrieval_only: '原文已定位',
  insufficient_evidence: '证据不足',
  verification_failed: '未通过内容已拦截',
  model_unavailable: '模型暂不可用',
  source_unavailable: '在线来源未连接',
  error: '请求失败',
}

export default function Home() {
  const [view, setView] = useState<'ask' | 'library'>('ask')
  const [health, setHealth] = useState<Health>()
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [documents, setDocuments] = useState<LegalDocument[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [activeEvidence, setActiveEvidence] = useState<Evidence[]>([])
  const [selected, setSelected] = useState<Evidence>()
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [toast, setToast] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)
  const pkulaw = providers.find((provider) => provider.provider === 'pkulaw')

  useEffect(() => {
    Promise.all([getHealth(), getDocuments(), getProviders()])
      .then(([healthData, documentData, providerData]) => {
        setHealth(healthData)
        setDocuments(documentData.documents)
        setProviders(providerData.providers)
      })
      .catch(() => setToast('后端尚未连接，请确认开发服务已完整启动。'))
  }, [])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 2600)
    return () => window.clearTimeout(timer)
  }, [toast])

  async function submitQuestion(value: string) {
    const question = value.trim()
    if (!question || loading) return
    setView('ask')
    setInput('')
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', content: question }])
    setLoading(true)
    try {
      const response = await askLaw(question, documents[0]?.document_id)
      setMessages((current) => [...current, {
        id: crypto.randomUUID(), role: 'assistant', content: response.answer,
        status: response.status, evidence: response.evidence, verification: response.verification,
        retrieval: response.retrieval,
      }])
      setActiveEvidence(response.evidence)
      setSelected(response.evidence[0])
    } catch (error) {
      const content = error instanceof Error ? error.message : '请求失败，请稍后重试。'
      setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'assistant', content, status: 'error' }])
      setActiveEvidence([])
      setSelected(undefined)
    } finally {
      setLoading(false)
    }
  }

  function newTask() {
    setMessages([])
    setActiveEvidence([])
    setSelected(undefined)
    setInput('')
    setView('ask')
  }

  function showCitation(citationId: number) {
    const item = activeEvidence.find((evidence) => evidence.citation_id === citationId)
    if (!item) return
    setSelected(item)
    window.setTimeout(() => {
      window.document.getElementById(`evidence-${citationId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 30)
  }

  async function copyEvidence(item: Evidence) {
    try {
      await navigator.clipboard.writeText(`${item.document_title}${item.article_label}\n${item.text}\n来源：${item.source_name}${item.source_url ? ` ${item.source_url}` : ''}`)
      setToast('原文与来源已复制')
    } catch {
      setToast('复制失败，请手动选择原文。')
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Scale size={22} /></div>
          <div><strong>律证</strong><span>法律文献证据工作台</span></div>
        </div>
        <button type="button" className="new-task-button" onClick={newTask}><Plus size={17} />新建检索任务</button>
        <nav className="main-nav" aria-label="主导航">
          <span className="nav-caption">工作空间</span>
          <button type="button" className={view === 'ask' ? 'active' : ''} onClick={() => setView('ask')}><FileSearch size={18} />证据问答<ChevronRight size={15} /></button>
          <button type="button" className={view === 'library' ? 'active' : ''} onClick={() => setView('library')}><Library size={18} />法律文库<ChevronRight size={15} /></button>
        </nav>
        <div className="sidebar-spacer" />
        <button type="button" className="sidebar-source" onClick={() => setView('library')}>
          <span className="source-icon"><BookOpen size={17} /></span>
          <span><small>当前法律文库</small><strong>{documents[0]?.title.replace('中华人民共和国', '') || '正在连接'}</strong><em>{health ? `${health.article_count.toLocaleString()} 条原文已核验入库` : '正在读取文献状态'}</em></span>
        </button>
        <div className="sidebar-foot"><ShieldCheck size={15} /><span>无证据，不生成法律结论</span></div>
      </aside>

      <main className="main-workspace">
        <div className="mobile-nav">
          <div className="brand"><div className="brand-mark"><Scale size={20} /></div><strong>律证</strong></div>
          <div><button type="button" className={view === 'ask' ? 'active' : ''} onClick={() => setView('ask')}>问答</button><button type="button" className={view === 'library' ? 'active' : ''} onClick={() => setView('library')}>文库</button></div>
        </div>
        <header className="workspace-header">
          <div>
            <div className="breadcrumb">律师研究工作台<span>/</span>{view === 'ask' ? '证据问答' : '法律文库'}</div>
            <h1>{view === 'ask' ? '法律文献证据问答' : '官方法律文库'}</h1>
          </div>
          <div className="connection-statuses">
            <button type="button" className={`model-status provider-status ${pkulaw?.configured ? 'is-online' : ''}`} onClick={() => setModelDialogOpen(true)}>
              <span className="status-dot" />
              {pkulaw ? (pkulaw.configured ? '北大法宝已配置' : '北大法宝未配置') : '法宝连接中'}
              <CircleHelp size={15} />
            </button>
            <button type="button" className={`model-status ${health?.model_configured ? 'is-online' : ''}`} onClick={() => setModelDialogOpen(true)}>
              <span className="status-dot" />
              {health ? (health.model_configured ? health.model : '原文检索模式') : '连接中'}
              <CircleHelp size={15} />
            </button>
          </div>
        </header>

        {view === 'library' ? (
          <LibraryView documents={documents} onCopy={copyEvidence} />
        ) : (
          <div className="research-grid">
            <section className="conversation-panel" aria-label="证据问答">
              <div className="conversation-scroll">
                {messages.length === 0 ? (
                  <div className="welcome-state">
                    <div className="welcome-symbol">§</div>
                    <span className="section-label">EVIDENCE BEFORE ANSWER</span>
                    <h2>从原文开始，<br />而不是从答案开始。</h2>
                    <p>输入法律问题、比较、归纳或改写任务。系统先定位官方法条，再让模型基于证据作答。</p>
                    <div className="example-list">
                      {examples.map((example) => (
                        <button type="button" key={example.title} onClick={() => void submitQuestion(example.query)}>
                          <span>{example.title}</span><p>{example.query}</p><ArrowUp size={16} />
                        </button>
                      ))}
                    </div>
                    <div className="scope-note"><ShieldCheck size={16} />本地官方《民法典》优先；{pkulaw?.configured ? '未命中时调用北大法宝 MCP' : '配置北大法宝后可扩展在线检索'}</div>
                  </div>
                ) : (
                  <div className="message-list">
                    {messages.map((message) => (
                      <div className={`message ${message.role}`} key={message.id}>
                        {message.role === 'user' ? (
                          <><span className="message-role">你的任务</span><p>{message.content}</p></>
                        ) : (
                          <>
                            <div className="assistant-heading">
                              <span className="assistant-mark"><Sparkles size={15} /></span>
                              <strong>证据助手</strong>
                              {message.status && <span className={`answer-status status-${message.status}`}>{message.status === 'verified' ? <CheckCircle2 size={13} /> : message.status === 'error' ? <AlertCircle size={13} /> : <ShieldCheck size={13} />}{statusLabels[message.status]}</span>}
                            </div>
                            {message.retrieval?.notice && <div className="retrieval-notice"><AlertCircle size={14} />{message.retrieval.notice}</div>}
                            <AnswerText text={message.content} onCitation={showCitation} />
                            {message.verification && (
                              <details className="verification-details">
                                <summary>查看校验记录</summary>
                                <ul>{message.verification.checks.map((check) => <li key={check.name}>{check.passed ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}<span><strong>{check.name}</strong>{check.detail}</span></li>)}</ul>
                              </details>
                            )}
                          </>
                        )}
                      </div>
                    ))}
                    {loading && <div className="loading-state" role="status"><LoaderCircle className="spin" size={20} /><div><strong>正在检索并核对官方原文</strong><span>如已配置模型，将在生成后执行独立引用校验</span></div></div>}
                    <div ref={chatEndRef} />
                  </div>
                )}
              </div>
              <form className="question-composer" onSubmit={(event) => { event.preventDefault(); void submitQuestion(input) }}>
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                      event.preventDefault(); void submitQuestion(input)
                    }
                  }}
                  placeholder="输入法律问题、条文比对或原文改写任务…"
                  aria-label="输入法律任务"
                  maxLength={2000}
                  rows={3}
                />
                <div className="composer-bottom">
                  <span><kbd>Ctrl</kbd>＋<kbd>Enter</kbd> 发送<span className="character-count">{input.length}/2000</span></span>
                  <button type="submit" className="send-button" disabled={!input.trim() || loading} aria-label="发送任务"><ArrowUp size={19} /></button>
                </div>
              </form>
              <p className="legal-disclaimer">检索结果与模型输出仅作研究辅助，正式使用前请由律师复核。</p>
            </section>
            <EvidencePanel evidence={activeEvidence} selectedId={selected?.article_id} onSelect={setSelected} onCopy={copyEvidence} />
          </div>
        )}
      </main>
      {modelDialogOpen && <ModelDialog health={health} provider={pkulaw} onClose={() => setModelDialogOpen(false)} />}
      {toast && <div className="toast" role="status"><CheckCircle2 size={17} />{toast}</div>}
    </div>
  )
}
