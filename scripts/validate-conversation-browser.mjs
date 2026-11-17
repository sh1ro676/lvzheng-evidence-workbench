import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { chromium } from 'playwright-core'

// All API responses are deterministic fixtures: no keys, commercial searches, or external network.
const port = 7198
const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { stdio: 'pipe' })
let logs = ''
vite.stderr.on('data', (data) => { logs += data })
let browser
const gates = new Map()
const waitForGate = (key) => new Promise((resolve) => gates.set(key, resolve))
const check = (passed) => ({ passed, checks: [{ name: '引用编号有效', passed, detail: '引用检查结果' }] })
const evidence = (title) => ({
  citation_id: 1, document_id: title, article_id: title, document_title: title,
  article_label: '第一条', section_path: [], text: `${title}正文`, source_name: '测试来源',
  source_url: 'https://example.com/source', snapshot_sha256: 'test', content_hash: 'test',
  published_at: '', effective_at: '', status: '测试', source_notice: '测试资料', match_type: 'exact', relevance: 1,
})

try {
  let ready = false
  for (let i = 0; i < 60; i += 1) {
    try { ready = (await fetch(`http://127.0.0.1:${port}`)).ok } catch { /* starting */ }
    if (ready) break
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  assert.ok(ready, `Vite did not start: ${logs}`)
  browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || (process.platform === 'win32'
      ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : '/usr/bin/chromium'),
    headless: true,
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  page.setDefaultTimeout(10000)
  const errors = []
  const mainRequests = []
  const schoolRequests = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/health') return route.fulfill({ json: { ok: true, model_configured: true, model: 'test', document_count: 0, article_count: 0 } })
    if (path === '/api/documents') return route.fulfill({ json: { documents: [] } })
    if (path === '/api/providers') return route.fulfill({ json: { providers: [] } })
    const body = route.request().postDataJSON()
    const question = body.question
    if (path.endsWith('/stream')) {
      mainRequests.push(body)
      if (question === '等待中的任务') await waitForGate('stream')
      const answer = `${question}的主回答。【证据1】`
      const payload = { done: true, status: 'verified', answer, evidence: [evidence(`${question}主证据`)],
        verification: check(true), followup: ['第一问', '第二问'].includes(question) ? 'schools' : null }
      return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify({ delta: answer })}\n\ndata: ${JSON.stringify(payload)}\n\n` }).catch(() => {})
    }
    if (path === '/api/schools') {
      schoolRequests.push(body)
      await waitForGate(question)
      return route.fulfill({ json: {
        status: 'verification_failed', section: `${question}补充观点。【证据1】`,
        evidence: [evidence(`${question}学校证据`)], verification: check(false),
      } }).catch(() => {})
    }
    return route.fulfill({ status: 404, json: {} })
  })
  await page.goto(`http://127.0.0.1:${port}`)
  const input = page.getByRole('textbox', { name: '输入法律任务' })
  async function submit(text) {
    await input.fill(text)
    await page.getByRole('button', { name: '发送任务' }).click()
    await page.locator('.message.assistant').last().locator('.answer-status').waitFor()
  }
  async function until(predicate) {
    for (let i = 0; i < 100; i += 1) {
      if (predicate()) return
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    throw new Error('Expected browser request was not received')
  }
  await submit('第一问')
  await until(() => gates.has('第一问'))
  await submit('第二问')
  await until(() => gates.has('第二问'))
  assert.equal(mainRequests[1].history[0].content, '第一问')
  assert.match(mainRequests[1].history[1].content, /第一问的主回答/)
  assert.deepEqual(schoolRequests[1].history, mainRequests[1].history)
  gates.get('第一问')()
  await page.locator('.message.assistant').first().locator('.status-verification_failed').waitFor()
  assert.equal(await page.locator('.message.assistant').last().locator('.status-verified').count(), 1)
  assert.equal(await page.locator('.evidence-panel').getByText(/第一问学校证据/).count(), 0)
  await page.locator('.message.assistant').first().getByRole('button', { name: '查看证据 2', exact: true }).click()
  await page.locator('.evidence-panel').getByText('第一问学校证据正文', { exact: true }).first().waitFor()
  await page.getByRole('button', { name: '新建法律任务' }).click()
  gates.get('第二问')()
  await submit('新任务')
  assert.deepEqual(mainRequests.at(-1).history, [])
  assert.equal(await page.locator('.message.assistant').count(), 1)
  assert.equal(await page.locator('.evidence-panel').getByText(/第二问/).count(), 0)
  await input.fill('等待中的任务')
  await page.getByRole('button', { name: '发送任务' }).click()
  await until(() => gates.has('stream'))
  await page.getByRole('button', { name: '新建法律任务' }).click()
  gates.get('stream')()
  await submit('最终任务')
  assert.deepEqual(mainRequests.at(-1).history, [])
  assert.equal(await page.locator('.message.assistant').count(), 1)
  assert.equal(await page.locator('.message.assistant').getByText(/等待中的任务/).count(), 0)
  assert.deepEqual(errors, [])
  console.log('PASS: follow-up context, combined verification, late supplement isolation, task reset and cancellation')
} finally {
  for (const resolve of gates.values()) resolve()
  await browser?.close()
  vite.kill()
}
