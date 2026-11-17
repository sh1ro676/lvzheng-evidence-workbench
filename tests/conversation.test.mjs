import assert from 'node:assert/strict'
import test from 'node:test'
import { loadTypescript } from './load-typescript.mjs'

const { conversationHistory, mergeSchoolResponse } = await loadTypescript(new URL('../src/lib/conversation.ts', import.meta.url))

const check = (passed) => ({ passed, checks: [{ name: '引用编号有效', passed, detail: '检查【证据1】' }] })
const main = () => ({
  id: 'a', role: 'assistant', content: '主回答【证据1】', status: 'verified',
  verification: check(true), evidence: [{ citation_id: 1, text: '主证据' }],
})
const school = (passed) => ({
  status: passed ? 'verified' : 'verification_failed', section: '观点【证据 1 】',
  evidence: [{ citation_id: 1, text: '学校证据' }], verification: check(passed),
})

test('failed school verification changes the whole answer status and preserves both check sets', () => {
  const answer = main()
  const result = mergeSchoolResponse(answer, school(false))
  assert.equal(result.status, 'verification_failed')
  assert.equal(result.verification.passed, false)
  assert.equal(result.verification.checks.length, 3)
  assert.match(result.verification.checks[0].name, /主回答/)
  assert.match(result.verification.checks[1].name, /四校补充/)
  assert.match(result.verification.checks[1].detail, /证据2/)
  assert.match(result.content, /观点【证据2】/)
  assert.deepEqual(result.evidence.map((item) => item.citation_id), [1, 2])
  assert.equal(answer.status, 'verified')
  assert.equal(answer.evidence.length, 1)
})

test('passing school checks cannot hide a failed main answer', () => {
  const result = mergeSchoolResponse({ ...main(), status: 'verification_failed', verification: check(false) }, school(true))
  assert.equal(result.status, 'verification_failed')
  assert.equal(result.verification.passed, false)
})

test('both passing sections retain verified status and an empty normal search does not fail the main answer', () => {
  assert.equal(mergeSchoolResponse(main(), school(true)).verification.passed, true)
  const empty = mergeSchoolResponse(main(), { status: 'insufficient_evidence', section: '', evidence: [] })
  assert.equal(empty.status, 'verified')
  assert.equal(empty.verification.checks.length, 1)
})

test('missing verification or a timeout cannot silently inherit green status', () => {
  const noCheck = { ...school(true), verification: undefined }
  assert.equal(mergeSchoolResponse(main(), noCheck).status, 'verification_failed')
  const timeout = mergeSchoolResponse(main(), {
    status: 'source_unavailable', section: '', evidence: [], verification: check(false),
  })
  assert.equal(timeout.status, 'verification_failed')
  assert.match(timeout.content, /主回答【证据1】/)
  assert.match(timeout.schoolNotice, /未完成/)
})

test('history includes completed pairs only and never reuses previous citation numbers', () => {
  const history = conversationHistory([
    { id: 'u', role: 'user', content: '解释缓刑' }, main(),
    { id: 'u2', role: 'user', content: '失败的问题' },
    { id: 'a2', role: 'assistant', content: '调用失败', status: 'error' },
    { id: 'u3', role: 'user', content: '尚未完成' },
    { id: 'a3', role: 'assistant', content: '生成中' },
  ])
  assert.deepEqual(history.map((item) => item.role), ['user', 'assistant'])
  assert.equal(history[0].content, '解释缓刑')
  assert.doesNotMatch(history[1].content, /【证据/)
  assert.deepEqual(conversationHistory([]), [])
})

test('history has a fixed size budget for long chats', () => {
  const messages = Array.from({ length: 50 }, (_, i) => [
    { id: `u${i}`, role: 'user', content: `用户${i}` },
    { ...main(), id: `a${i}`, content: '答'.repeat(20000) },
  ]).flat()
  const history = conversationHistory(messages)
  assert.ok(history.length <= 12)
  assert.ok(history.reduce((sum, item) => sum + item.content.length, 0) <= 24000)
  assert.equal(history[0].role, 'user')
})
