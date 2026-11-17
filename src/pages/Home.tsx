import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle, ArrowUp, BookOpen, CheckCircle2, ChevronRight, CircleHelp,
  FileSearch, Globe, Library, LoaderCircle, Plus, Scale, ShieldCheck, Sparkles,
} from 'lucide-react'
import { AnswerText } from '@/components/AnswerText'
import { EvidencePanel } from '@/components/EvidencePanel'
import { LibraryView } from '@/components/LibraryView'
import { ModelDialog } from '@/components/ModelDialog'
import { askLawStream, askSchools, getDocuments, getHealth, getProviders } from '@/lib/api'
import { conversationHistory, mergeSchoolResponse } from '@/lib/conversation'
import type { AgentStage } from '@/lib/api'
import type { AnswerStatus, ChatMessage, Evidence, Health, LegalDocument, ProviderStatus } from '@/types/legal'
import '../App.css'

const examples = [
  { title: '精确读取', query: '请给出《中华人民共和国民法典》第 1165 条原文，并解释其含义。' },
  { title: '对比分析', query: '对比第一千一百六十五条与第一千一百六十六条的归责原则。' },
  { title: '通俗改写', query: '把第一千零七十七条改写成一段面向当事人的通俗说明，保留关键期限。' },
  { title: '观点检索', query: '查找李永军教授关于《民法典》第 368 条居住权登记的公开观点，列明网页来源。' },
]

const statusLabels: Record<AnswerStatus, string> = {
  verified: '引用校验通过',
  retrieval_only: '原文已定位',
  insufficient_evidence: '证据不足',
  verification_failed: '回答已展示 · 引用需复核',
  model_unavailable: '模型暂不可用',
  source_unavailable: '在线来源未连接',
  error: '请求失败',
}

const stageLabels: Record<AgentStage, { title: string; detail: string }> = {
  planning: { title: '正在理解问题', detail: '判断任务类型，并确定需要核对的资料' },
  retrieving: { title: '正在并行检索资料', detail: '同时查询法条、教材与公开网页；首条网页查询有效即停止' },
  supplementing: { title: '正在补充相关法条', detail: '根据案件事实核对涉及罪名的法律原文' },
  generating: { title: '正在生成完整回答', detail: '正文会逐字显示，检索资料只作增强，不限制模型分析' },
  verifying: { title: '正在核对引用与来源', detail: '检查证据编号和直接引语，必要时自动修正' },
}

export default function Home() {
  const [view, setView] = useState<'ask' | 'library'>('ask')
  const [health, setHealth] = useState<Health>()
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [documents, setDocuments] = useState<LegalDocument[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [stage, setStage] = useState<AgentStage>('planning')
  const [webSearch, setWebSearch] = useState(true)
  const [activeEvidence, setActiveEvidence] = useState<Evidence[]>([])
  const [selected, setSelected] = useState<Evidence>()
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [toast, setToast] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)
  const inFlightRef = useRef(false)
  const taskVersionRef = useRef(0)
  const activeMessageIdRef = useRef<string | undefined>(undefined)
  const requestControllers = useRef(new Set<AbortController>())

  useEffect(() => {
    const controllers = requestControllers.current
    return () => controllers.forEach((controller) => controller.abort())
  }, [])
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
    if (!question || inFlightRef.current) return
    const taskVersion = taskVersionRef.current
    const history = conversationHistory(messages)
    const controller = new AbortController()
    requestControllers.current.add(controller)
    inFlightRef.current = true
    setView('ask')
    setInput('')
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: 'user', content: question }])
    setStage('planning')
    setLoading(true)
    const assistantId = crypto.randomUUID()
    activeMessageIdRef.current = assistantId
    try {
      // 先建立空回答，阶段事件显示检索进度，正文增量到达后直接流式渲染。
      setMessages((current) => [...current, {
        id: assistantId, role: 'assistant', content: '', following: false,
      }])
      const response = await askLawStream(question, (delta) => {
        if (taskVersion !== taskVersionRef.current) return
        setMessages((current) => current.map((message) => message.id === assistantId
          ? { ...message, content: message.content + delta }
          : message))
      }, (replacement) => {
        if (taskVersion !== taskVersionRef.current) return
        // 引用修正后整段覆盖，但不再用拦截说明替代回答。
        setMessages((current) => current.map((message) => message.id === assistantId
          ? { ...message, content: replacement }
          : message))
      }, webSearch, (next) => {
        if (taskVersion === taskVersionRef.current) setStage(next)
      }, history, controller.signal)
      if (taskVersion !== taskVersionRef.current) return
      setMessages((current) => current.map((message) => message.id === assistantId
        ? {
            ...message, content: response.answer || message.content,
            status: response.status, evidence: response.evidence,
            verification: response.verification, retrieval: response.retrieval,
            following: response.followup === 'schools',
          }
        : message))
      setActiveEvidence(response.evidence)
      setSelected(response.evidence[0])
      setLoading(false)

      // 法条、学理与案件均在后台独立补充四校观点，不重写已完成的主回答。
      if (response.followup === 'schools') {
        const schoolController = new AbortController()
        requestControllers.current.add(schoolController)
        askSchools(question, 180000, history, schoolController.signal).then((school) => {
          if (taskVersion !== taskVersionRef.current) return
          setMessages((current) => current.map((message) => message.id === assistantId
            ? mergeSchoolResponse(message, school) : message))
          // A late supplement belongs to its original answer, never to the next turn's evidence panel.
          if (activeMessageIdRef.current === assistantId) {
            const merged = mergeSchoolResponse({
              ...response, id: assistantId, role: 'assistant', content: response.answer,
            }, school)
            setActiveEvidence(merged.evidence ?? [])
            if (school.replace && school.section) setSelected(merged.evidence?.[0])
          }
        }).catch(() => {
          if (taskVersion !== taskVersionRef.current) return
          setMessages((current) => current.map((message) => message.id === assistantId
            ? mergeSchoolResponse(message, {
                status: 'source_unavailable', section: '', evidence: [],
                verification: { passed: false, checks: [{
                  name: '四校补充完成', passed: false,
                  detail: '公开资料与四校观点检索失败或超时，可稍后重试。',
                }] },
              }) : message))
        }).finally(() => requestControllers.current.delete(schoolController))
      }
    } catch (error) {
      if (taskVersion !== taskVersionRef.current) return
      setLoading(false)
      const content = error instanceof Error ? error.message : '请求失败，请稍后重试。'
      setMessages((current) => {
        const hasPlaceholder = current.some((message) => message.id === assistantId)
        if (!hasPlaceholder) {
          return [...current, { id: crypto.randomUUID(), role: 'assistant', content, status: 'error' }]
        }
        return current.map((message) => message.id === assistantId
          ? { ...message, content, status: 'error', following: false }
          : message)
      })
      setActiveEvidence([])
      setSelected(undefined)
    } finally {
      requestControllers.current.delete(controller)
      if (taskVersion === taskVersionRef.current) inFlightRef.current = false
    }
  }

  function newTask() {
    taskVersionRef.current += 1
    requestControllers.current.forEach((controller) => controller.abort())
    requestControllers.current.clear()
    activeMessageIdRef.current = undefined
    inFlightRef.current = false
    setLoading(false)
    setMessages([])
    setActiveEvidence([])
    setSelected(undefined)
    setInput('')
    setView('ask')
  }

  function showCitation(citationId: number, messageEvidence?: Evidence[], messageId?: string) {
    const evidence = messageEvidence ?? activeEvidence
    const item = evidence.find((entry) => entry.citation_id === citationId)
    if (!item) return
    activeMessageIdRef.current = messageId
    setActiveEvidence(evidence)
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
          <div><strong>律途</strong><span>法律 AI 助手</span></div>
        </div>
        <button type="button" className="new-task-button" onClick={newTask}><Plus size={17} />新建法律任务</button>
        <nav className="main-nav" aria-label="主导航">
          <span className="nav-caption">工作空间</span>
          <button type="button" className={view === 'ask' ? 'active' : ''} onClick={() => setView('ask')}><FileSearch size={18} />法律问答<ChevronRight size={15} /></button>
          <button type="button" className={view === 'library' ? 'active' : ''} onClick={() => setView('library')}><Library size={18} />法律文库<ChevronRight size={15} /></button>
        </nav>
        <div className="sidebar-spacer" />
        <button type="button" className="sidebar-source" onClick={() => setView('library')}>
          <span className="source-icon"><BookOpen size={17} /></span>
          <span><small>独立法律文库</small><strong>{documents.length > 1 ? `${documents.length} 部核心法律` : documents[0]?.title.replace('中华人民共和国', '') || '正在连接'}</strong><em>{health ? `${health.article_count.toLocaleString()} 条原文可供单独核验` : '正在读取文献状态'}</em></span>
        </button>
        <div className="sidebar-foot"><ShieldCheck size={15} /><span>模型完整分析，资料辅助核验</span></div>
      </aside>

      <main className="main-workspace">
        <div className="mobile-nav">
          <div className="brand"><div className="brand-mark"><Scale size={20} /></div><strong>律途</strong></div>
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
                    <span className="section-label">LEGAL AGENT WITH SOURCES</span>
                    <h2>先理解你的问题，<br />再用法律知识回答。</h2>
                    <p>可以直接询问案件、法律概念、实务分析或文书任务。模型会完整组织回答，北大法宝、教材和公开网页用于补充专业知识与核对出处。</p>
                    <div className="example-list">
                      {examples.map((example) => (
                        <button type="button" key={example.title} onClick={() => void submitQuestion(example.query)}>
                          <span>{example.title}</span><p>{example.query}</p><ArrowUp size={16} />
                        </button>
                      ))}
                    </div>
                    <div className="scope-note"><ShieldCheck size={16} />模型分析不被检索结果限制；具体事实与引用来源单独标注</div>
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
                            <AnswerText
                              text={message.content}
                              evidence={message.evidence ?? []}
                              webSearch={message.schoolRetrieval ?? message.retrieval?.web_search}
                              onCitation={(citationId) => showCitation(citationId, message.evidence ?? [], message.id)}
                            />
                            {message.retrieval?.web_search?.enabled && (
                              <details className="verification-details web-search-details">
                                <summary>联网检索记录 · {message.retrieval.web_search.fetched_count} 个有效网页 · {message.retrieval.web_search.search_count} 次搜索</summary>
                                {message.retrieval.web_search.queries.length > 0 && <div className="web-search-queries">实际检索词：{message.retrieval.web_search.queries.join('；')}</div>}
                                <ul>{message.retrieval.web_search.events.map((event, index) => <li key={`${event.stage}-${index}`}>{event.ok ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}<span><strong>{event.institution ? `${event.institution} · ` : ''}{({ plan: '检索规划', search: '网页搜索', candidate: '搜索候选', retry: '换词重试', fetch: '正文抓取', filter: '筛选排除', evidence: '纳入证据', entity: '人物线索', second_round: '人物精确复核' } as const)[event.stage]}</strong>{(event.entity ? `发现人物：${event.entity}` : '') || event.error || event.reason || (event.stage === 'search' && event.result_count !== undefined ? `找到 ${event.result_count} 个结果` : '') || (event.title ? `${event.title} · ${event.url}` : '') || event.query || event.url || '已完成'}</span></li>)}</ul>
                              </details>
                            )}
                            {message.following && <div className="retrieval-notice"><LoaderCircle className="spin" size={14} />正在后台检索四校观点，完成后自动追加到本节下方</div>}
                            {message.schoolNotice && <div className="retrieval-notice"><AlertCircle size={14} />{message.schoolNotice}</div>}
                            {message.schoolRetrieval?.enabled && (
                              <details className="verification-details web-search-details">
                                <summary>四校检索记录 · {message.schoolRetrieval.fetched_count} 个有效网页 · {message.schoolRetrieval.search_count} 次搜索</summary>
                                {message.schoolRetrieval.institution_results && (
                                  <div className="web-search-queries">
                                    各校命中：{message.schoolRetrieval.institution_results.map((item) => `${item.institution}${item.found ? '✓' : '×'}`).join(' · ')}
                                  </div>
                                )}
                                {message.schoolRetrieval.queries.length > 0 && <div className="web-search-queries">实际检索词：{message.schoolRetrieval.queries.join('；')}</div>}
                                <ul>{message.schoolRetrieval.events.map((event, index) => <li key={`${event.stage}-${index}`}>{event.ok ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}<span><strong>{event.institution ? `${event.institution} · ` : ''}{({ plan: '检索规划', search: '网页搜索', candidate: '搜索候选', retry: '换词重试', fetch: '正文抓取', filter: '筛选排除', evidence: '纳入证据', entity: '人物线索', second_round: '人物精确复核' } as const)[event.stage]}</strong>{(event.entity ? `发现人物：${event.entity}` : '') || event.error || event.reason || (event.stage === 'search' && event.result_count !== undefined ? `找到 ${event.result_count} 个结果` : '') || (event.title ? `${event.title} · ${event.url}` : '') || event.query || event.url || '已完成'}</span></li>)}</ul>
                              </details>
                            )}
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
                    {loading && <div className="loading-state" role="status" data-stage={stage}><LoaderCircle className="spin" size={20} /><div><strong>{stageLabels[stage].title}</strong><span>{stageLabels[stage].detail}</span></div></div>}
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
                  <div className="composer-tools">
                    <button type="button" className={`web-toggle ${webSearch ? 'active' : ''}`} onClick={() => setWebSearch((enabled) => !enabled)} aria-pressed={webSearch} title="案件问题检索公开事实，法条问题可后台补充四校观点"><Globe size={15} />联网增强<span>{webSearch ? '案件与四校' : '已关闭'}</span></button>
                    <span className="keyboard-hint"><kbd>Ctrl</kbd>＋<kbd>Enter</kbd> 发送<span className="character-count">{input.length}/2000</span></span>
                  </div>
                  <button type="submit" className="send-button" disabled={!input.trim() || loading} aria-label="发送任务"><ArrowUp size={19} /></button>
                </div>
              </form>
              <p className="legal-disclaimer">检索结果与模型输出仅作研究辅助，正式使用前请由律师复核。</p>
            </section>
            <EvidencePanel evidence={activeEvidence} selectedCitationId={selected?.citation_id} onSelect={setSelected} onCopy={copyEvidence} />
          </div>
        )}
      </main>
      {modelDialogOpen && <ModelDialog health={health} provider={pkulaw} onClose={() => setModelDialogOpen(false)} />}
      {toast && <div className="toast" role="status"><CheckCircle2 size={17} />{toast}</div>}
    </div>
  )
}
