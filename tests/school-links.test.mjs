import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { loadTypescript } from './load-typescript.mjs'

const { InstitutionAnswerSection } = await loadTypescript(
  new URL('../src/components/InstitutionAnswerSection.tsx', import.meta.url),
)
const { EvidencePanel } = await loadTypescript(
  new URL('../src/components/EvidencePanel.tsx', import.meta.url),
)

const SCHOOL_EVIDENCE = [{
  citation_id: 2, document_id: 'opinion', article_id: 'page-2', document_title: '监护观点',
  article_label: '公开网页', section_path: [], text: '谢澍教授认为成年监护应尊重本人意愿。',
  provider: 'deepseek_web', match_type: 'web', relevance_basis: 'article_text',
  source_url: 'https://example.edu.cn/opinion-2', source_name: '观点来源',
  institution: '中国政法大学', status: '网页正文',
}]

test('a school card offers a jump to its evidence on the right', () => {
  const clicked = []
  const html = renderToStaticMarkup(createElement(InstitutionAnswerSection, {
    lines: ['中国政法大学', '谢澍教授认为成年监护应尊重本人意愿。【证据2】'],
    evidence: SCHOOL_EVIDENCE,
    webSearch: { institution_results: [] },
    onCitation: (id) => clicked.push(id),
    renderParagraph: (text, key) => createElement('p', { key }, text),
  }))
  assert.match(html, /点此核对原文/)
  assert.match(html, /证据 2/)
})

test('an unselected evidence item still exposes its source link', () => {
  const html = renderToStaticMarkup(createElement(EvidencePanel, {
    evidence: SCHOOL_EVIDENCE,
    selectedCitationId: 99,
    onSelect() {},
    onCopy() {},
  }))
  assert.match(html, /https:\/\/example\.edu\.cn\/opinion-2/)
  assert.match(html, /打开网页原文/)
})
