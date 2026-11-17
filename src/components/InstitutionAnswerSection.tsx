import type { ReactNode } from 'react'
import { BookOpen, GraduationCap, SearchX, University } from 'lucide-react'
import type { Evidence, WebSearchInfo } from '@/types/legal'

const INSTITUTIONS = ['中国政法大学', '西北政法大学', '北京大学法学院', '清华大学法学院'] as const
type Institution = typeof INSTITUTIONS[number]

interface AnswerBlock {
  institution?: Institution
  title: string
  suffix?: string
  lines: string[]
}

interface InstitutionAnswerSectionProps {
  lines: string[]
  evidence: Evidence[]
  webSearch: WebSearchInfo
  onCitation: (citationId: number) => void
  renderParagraph: (text: string, key: string) => ReactNode
}

function parseSchoolHeading(line: string): (Pick<AnswerBlock, 'institution' | 'title' | 'suffix'> & { content?: string }) | undefined {
  const normalized = line.trim()
    .replace(/^#{1,6}\s*/, '')
    .replace(/^\*\*(.+)\*\*$/, '$1')
    .trim()
  const match = normalized.match(/^(?:(?:[（(](?:[一二三四五六七八九十]+|\d+)[）)])|(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*(.+)$/)
  if (!match) return undefined
  const aliases: [Institution, string][] = [
    ['中国政法大学', '中国政法大学'],
    ['西北政法大学', '西北政法大学'],
    ['北京大学法学院', '北京大学法学院'],
    ['北京大学法学院', '北京大学'],
    ['清华大学法学院', '清华大学法学院'],
    ['清华大学法学院', '清华大学'],
  ]
  const matched = aliases.find(([, name]) => match[1].startsWith(name))
  if (!matched) return undefined
  const [institution, matchedName] = matched
  let rest = match[1].slice(matchedName.length)
  const hasExplicitPrefix = /^(?:[（(](?:[一二三四五六七八九十]+|\d+)[）)]|[一二三四五六七八九十]+、|\d+[.、])/.test(normalized)
  // 无编号时学校名后必须有标题边界；兼容旧格式“（一）北京大学尹田教授”。
  if (rest && !hasExplicitPrefix && !/^(?:[\s:：—–，,]|方面)/.test(rest)) return undefined
  rest = rest.replace(/^[\s:：—–-]+/, '').replace(/^方面[\s:：—–，,-]*/, '').trim()
  // 模型常把“学校方面，观点……【证据N】”写在同一行，不能再要求标题与正文分离。
  const hasInlineContent = rest.includes('【证据') || /[。！？]/.test(rest) || rest.length > 40
  return {
    institution,
    title: institution,
    suffix: hasInlineContent ? undefined : rest || undefined,
    content: hasInlineContent ? rest : undefined,
  }
}

function isSubheading(line: string) {
  return /^[（(](?:[一二三四五六七八九十]+|\d+)[）)]/.test(line.trim())
}

function parseBlocks(lines: string[]) {
  const intro: string[] = []
  const blocks: AnswerBlock[] = []
  let current: AnswerBlock | undefined

  lines.forEach((line) => {
    if (!line.trim()) return
    const schoolHeading = parseSchoolHeading(line)
    if (schoolHeading) {
      const { content, ...heading } = schoolHeading
      current = { ...heading, lines: content ? [content] : [] }
      blocks.push(current)
    } else if (isSubheading(line)) {
      current = { title: line.trim(), lines: [] }
      blocks.push(current)
    } else if (current) {
      current.lines.push(line)
    } else {
      intro.push(line)
    }
  })
  return { intro, blocks }
}

function citationIds(lines: string[]) {
  return new Set(
    Array.from(lines.join('\n').matchAll(/【证据\s*(\d+)\s*】/g)).map((match) => Number(match[1])),
  )
}

export function InstitutionAnswerSection({ lines, evidence, webSearch, renderParagraph }: InstitutionAnswerSectionProps) {
  const { intro, blocks } = parseBlocks(lines)
  const validatedBlocks = new Set<AnswerBlock>()

  INSTITUTIONS.forEach((institution) => {
    blocks.filter((block) => block.institution === institution).forEach((block) => {
      const ids = citationIds(block.lines)
      const hasMatchingEvidence = evidence.some((item) => item.institution === institution && ids.has(item.citation_id))
      if (hasMatchingEvidence && block.lines.some((line) => line.trim())) validatedBlocks.add(block)
    })
  })

  const unclassifiedBlocks = blocks.filter((block) => !validatedBlocks.has(block))
  const hitCount = INSTITUTIONS.filter((institution) => evidence.some((item) => item.institution === institution)).length

  return (
    <section className="institution-answer" aria-label="四校教授观点分区">
      {intro.length > 0 && <div className="institution-intro">{intro.map((line, index) => renderParagraph(line, `intro-${index}`))}</div>}
      <div className="institution-summary">
        <span><University size={16} />四校定向检索</span>
        <span><strong>{hitCount}</strong> / 4 所有可展示资料</span>
        <small>排列顺序不代表学术评价</small>
      </div>

      <div className="school-opinion-list">
        {INSTITUTIONS.map((institution) => {
          const schoolEvidence = evidence.filter((item) => item.institution === institution)
          const schoolBlocks = blocks.filter((block) => block.institution === institution && validatedBlocks.has(block))
          const result = webSearch.institution_results?.find((item) => item.institution === institution)
          const suffixes = [...new Set(schoolBlocks.map((block) => block.suffix).filter(Boolean))]
          const hasDirect = schoolEvidence.some((item) => item.relevance_basis === 'article_text')
          const hasTopic = schoolEvidence.some((item) => item.relevance_basis === 'topic_paper' || item.relevance_basis === 'topic_related')
          const hasUncategorizedEvidence = schoolEvidence.some((item) => !item.relevance_basis)

          return (
            <article className={`school-opinion ${schoolEvidence.length > 0 ? 'is-hit' : 'is-empty'}`} key={institution} aria-label={`${institution}检索结果`}>
              <header className="school-opinion-header">
                <div className="school-identity">
                  <span className="school-emblem" aria-hidden="true"><University size={21} strokeWidth={1.7} /></span>
                  <div>
                    <h3 className="school-name">{institution}</h3>
                    {suffixes.length > 0 && <p className="professor-line"><GraduationCap size={15} /><strong>{suffixes.join('、')}</strong></p>}
                  </div>
                </div>
                <div className="school-badges">
                  {hasDirect && <span className="relevance-badge is-direct">正文提及所问条号</span>}
                  {hasTopic && <span className="relevance-badge is-topic">主题相关资料</span>}
                  {hasUncategorizedEvidence && <span className="relevance-badge">公开网页正文</span>}
                  {schoolEvidence.length === 0 && <span className="relevance-badge is-missed">{result?.found === false ? '本次未命中' : result?.found ? '记录命中' : '状态未知'}</span>}
                </div>
              </header>

              {schoolBlocks.length > 0 ? (
                <div className="opinion-body">
                  {schoolBlocks.map((block, blockIndex) => block.lines.map((line, lineIndex) => renderParagraph(line, `${institution}-${blockIndex}-${lineIndex}`)))}
                  {hasTopic && <p className="topic-limit-note"><BookOpen size={15} />主题相关资料未逐字引用所问条号，不作为逐条释义依据。</p>}
                </div>
              ) : (
                <div className="school-opinion-empty">
                  <SearchX size={18} strokeWidth={1.6} />
                  <div>
                    {schoolEvidence.length > 0 ? (
                      <p>已检索到 {schoolEvidence.length} 份该校资料，但原回答未形成可核验的学校小节，请在右侧原文证据中查看。</p>
                    ) : result?.found === false ? (
                      <p>本次检索未获得该校教授针对本问题的可核验公开观点，不代表该校没有相关研究。</p>
                    ) : result?.found ? (
                      <p>检索记录显示该校有命中，但证据列表未返回正文，请打开检索记录核查。</p>
                    ) : (
                      <p>本次未返回该校的明确检索状态，请查看联网检索记录。</p>
                    )}
                    {result?.search_count !== undefined && <span>实际完成 {result.search_count} 次定向搜索{result.content_fetched_count !== undefined ? ` · 成功读取 ${result.content_fetched_count} 个网页正文` : ''}</span>}
                  </div>
                </div>
              )}
            </article>
          )
        })}
      </div>

      {unclassifiedBlocks.length > 0 && (
        <div className="unclassified-answer">
          <h4>未按学校归类的原回答</h4>
          <p className="unclassified-note">以下内容的学校归属未由对应引用确认，因此保留原文但不放入学校卡片。</p>
          {unclassifiedBlocks.map((block, blockIndex) => (
            <div key={`${block.title}-${blockIndex}`}>
              <h5>{block.title}</h5>
              {block.lines.map((line, lineIndex) => renderParagraph(line, `unclassified-${blockIndex}-${lineIndex}`))}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
