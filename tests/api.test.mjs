import assert from 'node:assert/strict'
import test from 'node:test'
import { loadTypescript } from './load-typescript.mjs'

const { askLawStream, askSchools } = await loadTypescript(new URL('../src/lib/api.ts', import.meta.url))

test('main stream and school requests transmit the same prior turns', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const history = [{ role: 'user', content: '此前的案件' }]
  globalThis.fetch = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) })
    return url.endsWith('/stream')
      ? new Response('data: {"delta":"正文"}\n\ndata: {"done":true,"answer":"正文"}\n\n')
      : Response.json({ section: '' })
  }
  try {
    let answer = ''
    await askLawStream('那之后呢', (delta) => { answer += delta }, undefined, true, undefined, history)
    await askSchools('那之后呢', 1000, history)
    assert.equal(answer, '正文')
    assert.deepEqual(calls.map((call) => call.body.history), [history, history])
    assert.equal(calls[0].body.question, '那之后呢')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('school requests can be cancelled when starting a new task', async () => {
  const originalFetch = globalThis.fetch
  const controller = new AbortController()
  globalThis.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true })
  })
  try {
    const pending = askSchools('问题', 1000, [], controller.signal)
    controller.abort()
    await assert.rejects(pending, { name: 'AbortError' })
  } finally {
    globalThis.fetch = originalFetch
  }
})
