import type { ReactNode } from 'react'
import { InstitutionAnswerSection } from '@/components/InstitutionAnswerSection'
import type { Evidence, WebSearchInfo } from '@/types/legal'

interface AnswerTextProps {
  text: string
  onCitation: (citationId: number) => void
  evidence?: Evidence[]
  webSearch?: WebSearchInfo
}

interface AnswerParagraphProps {
  text: string
  onCitation: (citationId: number) => void
}

function AnswerParagraph({ text, onCitation }: AnswerParagraphProps) {
  return (
    <>
      {text.split(/(【证据\s*\d+\s*】|\*\*[^*\n]+\*\*)/g).map((part, index) => {
        const match = part.match(/【证据\s*(\d+)\s*】/)
        if (match) {
          const citationId = Number(match[1])
          return (
            <button
              type="button"
              className="inline-citation"
              key={index}
              onClick={() => onCitation(citationId)}
              aria-label={`查看证据 ${citationId}`}
            >
              {citationId}
            </button>
          )
        }
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={index}>{part.slice(2, -2)}</strong>
        }
        return part
      })}
    </>
  )
}

const topHeadingPattern = /^(?:[一二三四五六七八九十]+、|\d+[.、])(.+)$/
const headingPattern = /^(?:[一二三四五六七八九十]+、|\d+[.、]|[（(](?:[一二三四五六七八九十]+|\d+)[）)])[^【】\n]{2,60}$/

function normalizeLine(line: string) {
  return line.trim()
    .replace(/^#{1,6}\s*/, '')
    .replace(/^\*\*(.+)\*\*$/, '$1')
    .trim()
}

function isInstitutionHeading(line: string) {
  const normalized = line.trim().replace(/\s/g, '')
  if (normalized === '四校教授观点') return true
  const match = line.trim().match(topHeadingPattern)
  return match?.[1].replace(/\s/g, '') === '四校教授观点'
}

function isNumberedSchoolHeading(line: string) {
  return /^(?:[一二三四五六七八九十]+、|\d+[.、])(?:中国政法大学|西北政法大学|北京大学|清华大学)/.test(line)
}

export function AnswerText({ text, onCitation, evidence = [], webSearch }: AnswerTextProps) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const rendered: ReactNode[] = []
  const renderParagraph = (paragraph: string, key: string) => (
    <p key={key}><AnswerParagraph text={paragraph} onCitation={onCitation} /></p>
  )

  let index = 0
  while (index < lines.length) {
    const trimmed = normalizeLine(lines[index])
    if (!trimmed) {
      index += 1
      continue
    }

    if (webSearch?.mode === 'four_institutions' && isInstitutionHeading(trimmed)) {
      rendered.push(<h3 className="answer-heading institution-section-title" key={`heading-${index}`}>{trimmed}</h3>)
      let end = index + 1
      while (end < lines.length) {
        const nextLine = normalizeLine(lines[end])
        if (topHeadingPattern.test(nextLine) && !isNumberedSchoolHeading(nextLine)) break
        end += 1
      }
      rendered.push(
        <InstitutionAnswerSection
          key={`institutions-${index}`}
          lines={lines.slice(index + 1, end)}
          evidence={evidence}
          webSearch={webSearch}
          onCitation={onCitation}
          renderParagraph={renderParagraph}
        />,
      )
      index = end
      continue
    }

    if (headingPattern.test(trimmed)) {
      rendered.push(<h3 className="answer-heading" key={`heading-${index}`}>{trimmed}</h3>)
    } else {
      rendered.push(renderParagraph(trimmed, `paragraph-${index}`))
    }
    index += 1
  }

  return <div className="answer-text">{rendered}</div>
}
