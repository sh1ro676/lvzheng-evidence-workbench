import type { ChatMessage, ChatTurn, SchoolResponse, Verification } from '../types/legal'

/** Only completed turns enter the model context; citations are local to each answer. */
export function conversationHistory(messages: ChatMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  let pendingUser: ChatMessage | undefined
  for (const message of messages) {
    if (message.role === 'user') {
      pendingUser = message
    } else if (pendingUser && message.content && message.status
      && !['error', 'model_unavailable', 'source_unavailable'].includes(message.status)) {
      turns.push({ role: 'user', content: pendingUser.content }, { role: 'assistant', content: message.content })
      pendingUser = undefined
    }
  }
  let remaining = 24000
  const history: ChatTurn[] = []
  for (const turn of turns.slice(-12).reverse()) {
    const content = turn.content.slice(0, Math.min(6000, remaining))
      .replace(/【证据\s*\d+\s*】/g, '[历史引用]')
    if (content) history.unshift({ ...turn, content })
    remaining -= content.length
    if (remaining <= 0) break
  }
  while (history[0]?.role === 'assistant') history.shift()
  return history
}

function shiftCitations(text: string, offset: number) {
  return text.replace(/【证据\s*(\d+)\s*】/g, (_all, n: string) => `【证据${Number(n) + offset}】`)
}

function combinedVerification(main: Verification | undefined, supplement: Verification): Verification {
  return {
    passed: main?.passed === true && supplement.passed,
    checks: [
      ...(main?.checks ?? [{ name: '校验结果', passed: false, detail: '未取得主回答校验结果' }])
        .map((check) => ({ ...check, name: `主回答 · ${check.name}` })),
      ...supplement.checks.map((check) => ({ ...check, name: `四校补充 · ${check.name}` })),
    ],
  }
}

export function mergeSchoolResponse(message: ChatMessage, school: SchoolResponse): ChatMessage {
  const offset = school.replace ? 0 : (message.evidence?.length ?? 0)
  const evidence = school.evidence.map((item) => ({ ...item, citation_id: item.citation_id + offset }))
  const section = shiftCitations(school.section, offset)
  let supplement = school.verification
    ? { ...school.verification, checks: school.verification.checks.map((check) => ({
        ...check, detail: shiftCitations(check.detail, offset),
      })) }
    : undefined
  if (section && (!supplement || school.status !== 'verified')) {
    supplement = { passed: false, checks: [
      ...(supplement?.checks ?? []),
      { name: '补充回答状态', passed: false, detail: '四校补充未通过校验，内容请复核' },
    ] }
  }
  const verification = school.replace && section ? supplement
    : supplement ? combinedVerification(message.verification, supplement) : message.verification
  // An empty search is not a failed legal answer, but appended unchecked text cannot inherit green status.
  const status = school.replace && section ? school.status
    : supplement && !verification?.passed ? 'verification_failed' : message.status
  const tail = section
    ? `\n\n## ${school.section_title || '四校教授观点'}\n\n${section}`
    : '\n\n## 四校教授观点\n\n本次未取得可用的四校补充内容，主回答保持不变。'
  return {
    ...message, following: false, status, verification,
    content: school.replace && section ? section : message.content + tail,
    evidence: school.replace && section ? evidence : [...(message.evidence ?? []), ...evidence],
    schoolRetrieval: school.retrieval?.web_search,
    schoolNotice: supplement && !supplement.passed ? '四校补充未通过校验或未完成，请查看校验明细。' : undefined,
  }
}
