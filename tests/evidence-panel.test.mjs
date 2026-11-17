import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { loadTypescript } from './load-typescript.mjs'

const { EvidencePanel } = await loadTypescript(new URL('../src/components/EvidencePanel.tsx', import.meta.url))

test('a linked identity page is visible separately from the opinion and does not claim direct article interpretation', () => {
  const evidence = [{
    citation_id: 1, document_id: 'opinion', article_id: 'page', document_title: '监护观点',
    article_label: '主题相关网页', section_path: [], text: '观点原文保持独立。',
    provider: 'deepseek_web', match_type: 'web', relevance_basis: 'topic_related',
    source_url: 'https://example.com/opinion', source_name: '观点来源', source_notice: '出处待核',
    evidence_tier: 'secondary_unverified', status: '网页正文',
    identity_sources: [{ name: '张三', institution: '中国政法大学', source_url: 'https://example.edu.cn/profile',
      document_title: '张三教师简介', excerpt: '张三是中国政法大学教授。', content_hash: 'hash' }],
  }]
  const html = renderToStaticMarkup(createElement(EvidencePanel, {
    evidence, selectedCitationId: 1, onSelect() {}, onCopy() {},
  }))
  assert.match(html, /观点原文保持独立/)
  assert.match(html, /主题相关网页/)
  assert.match(html, /不作为所问条号的逐条释义/)
  assert.match(html, /身份核验/)
  assert.match(html, /https:\/\/example\.edu\.cn\/profile/)
  assert.match(html, /此页仅核验任职关联/)
  assert.match(html, /转述待核/)
})
