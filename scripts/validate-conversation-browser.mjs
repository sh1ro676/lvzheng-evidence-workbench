import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { execFileSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

// All API responses are deterministic fixtures: no keys, commercial searches, or external network.
const port = Number(process.env.VALIDATION_PORT || 7198)
const outputDir = fileURLToPath(new URL('../.validation/browser/', import.meta.url))
await mkdir(outputDir, { recursive: true })
const report = {
  mode: 'fixture_browser', status: 'running', started_at: new Date().toISOString(),
  tested_commit: process.env.TESTED_COMMIT || execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  checks: [],
}
const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { stdio: 'pipe' })
let logs = ''
vite.stderr.on('data', (data) => { logs += data })
vite.stdout.on('data', (data) => { logs += data })
let browser
let context
let page
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
  for (let i = 0; i < 200; i += 1) {
    if (vite.exitCode !== null) throw new Error(`Vite exited: ${logs}`)
    try { ready = (await fetch(`http://127.0.0.1:${port}`)).ok } catch { /* starting */ }
    if (ready) break
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  assert.ok(ready, `Vite did not start: ${logs}`)
  browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || undefined,
    headless: true,
  })
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
  page = await context.newPage()
  page.setDefaultTimeout(20000)
  const errors = []
  const mainRequests = []
  const schoolRequests = []
  const libraryRequests = []
  const books = ['上册', '下册'].map((volume, index) => ({
    book_id: `fixture-book-${index}`, title: `刑法学（第六版）· ${volume}`,
    source_name: '张明楷《刑法学》（第六版）· 法律出版社', page_count: 1, chunk_count: 1,
  }))
  page.on('pageerror', (error) => errors.push(error.message))
  await context.route('**/api/**', async (route) => {
    const { pathname: path, searchParams } = new URL(route.request().url())
    if (path === '/api/health') return route.fulfill({ json: { ok: true, model_configured: true, model: 'test' } })
    if (path === '/api/expert-library/books') return route.fulfill({ json: { books } })
    if (path === '/api/expert-library/search') {
      const book = books.find((item) => item.book_id === searchParams.get('book_id'))
      if (!book) return route.fulfill({ status: 404, json: { error: '测试教材不存在' } })
      libraryRequests.push({ bookId: book.book_id, query: searchParams.get('q') })
      return route.fulfill({ json: { evidence: [{
        ...evidence(book.title), article_label: '书内 p397（PDF p418）',
        provider: 'textbook', match_type: 'textbook', source_name: book.source_name, source_url: '',
        published_at: '2021',
      }] } })
    }
    if (path === '/api/providers') return route.fulfill({ json: { providers: [] } })
    const body = route.request().postDataJSON()
    const question = body.question
    if (path.endsWith('/stream')) {
      mainRequests.push(body)
      if (question === '等待中的任务') await waitForGate('stream')
      const answer = `${question}的主回答。【证据1】`
      const payload = { done: true, status: 'verified', answer, evidence: [evidence(`${question}主证据`)],
        verification: check(true), followup: ['第一问', '第二问'].includes(question) ? 'schools' : null }
      if (question === '身份核验') {
        payload.evidence[0] = { ...payload.evidence[0], provider: 'deepseek_web',
          relevance_basis: 'topic_related', evidence_tier: 'secondary_unverified',
          identity_sources: [{ name: '测试学者', institution: '中国政法大学',
            source_url: 'https://example.edu.cn/profile', document_title: '测试学者教师简介',
            excerpt: '这是用于界面测试的任职记载。', content_hash: 'fixture' }] }
      }
      return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify({ delta: answer })}\n\ndata: ${JSON.stringify(payload)}\n\n` }).catch(() => {})
    }
    if (path === '/api/schools') {
      schoolRequests.push(body)
      await waitForGate(question)
      return route.fulfill({ json: {
        status: 'verification_failed', section: `${question}补充观点。【证据1】`,
        evidence: [evidence(`${question}学校证据`)], verification: check(false),
        retrieval: { web_search: { enabled: true, queries: [], events: [], fetched_count: 1, search_count: 2,
          research_leads: [{ title: '未确认任职的相关网页', source_url: 'https://example.com/lead',
            excerpt: '这是需要人工核对的网页摘录。', search_institution: '北京大学法学院' }] } },
      } }).catch(() => {})
    }
    return route.fulfill({ status: 404, json: {} })
  })
  await page.goto(`http://127.0.0.1:${port}`)
  const input = page.getByRole('textbox', { name: '输入法律任务' })
  async function submit(text, target = page) {
    const index = await target.locator('.message.assistant').count()
    await target.getByRole('textbox', { name: '输入法律任务' }).fill(text)
    await target.getByRole('button', { name: '发送任务' }).click()
    await target.locator('.message.assistant').nth(index).locator('.answer-status').waitFor()
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
  report.checks.push('followup_history')
  gates.get('第一问')()
  await page.locator('.message.assistant').first().locator('.status-verification_failed').waitFor()
  assert.equal(await page.locator('.message.assistant').last().locator('.status-verified').count(), 1)
  assert.equal(await page.locator('.evidence-panel').getByText(/第一问学校证据/).count(), 0)
  report.checks.push('combined_verification_and_late_supplement_isolation')
  await page.locator('.message.assistant').first().getByRole('button', { name: '查看证据 2', exact: true }).click()
  await page.locator('.evidence-panel').getByText('第一问学校证据正文', { exact: true }).first().waitFor()
  report.checks.push('shifted_citation_opens_correct_evidence')
  const firstAnswer = page.locator('.message.assistant').first()
  await firstAnswer.getByText('待核线索 · 1 份相关网页', { exact: true }).click()
  await firstAnswer.getByText(/尚无法核实作者与检索院校的任职关系/).waitFor()
  await firstAnswer.getByText('这是需要人工核对的网页摘录。', { exact: true }).waitFor()
  const link = firstAnswer.getByRole('link', { name: '未确认任职的相关网页' })
  assert.equal(await link.getAttribute('href'), 'https://example.com/lead')
  assert.equal(await page.locator('.evidence-panel').getByText('未确认任职的相关网页').count(), 0)
  assert.equal(await firstAnswer.getByRole('button', { name: '查看证据 3' }).count(), 0)
  report.checks.push('research_leads_stay_outside_citable_evidence')
  await page.screenshot({ path: `${outputDir}/research-leads.png`, fullPage: true })
  await page.getByRole('button', { name: '新建法律任务' }).click()
  gates.get('第二问')()
  await submit('新任务')
  assert.deepEqual(mainRequests.at(-1).history, [])
  assert.equal(await page.locator('.message.assistant').count(), 1)
  assert.equal(await page.locator('.evidence-panel').getByText(/第二问/).count(), 0)
  report.checks.push('new_task_clears_history_and_old_supplements')
  await input.fill('等待中的任务')
  await page.getByRole('button', { name: '发送任务' }).click()
  await until(() => gates.has('stream'))
  await page.getByRole('button', { name: '新建法律任务' }).click()
  gates.get('stream')()
  await submit('最终任务')
  assert.deepEqual(mainRequests.at(-1).history, [])
  assert.equal(await page.locator('.message.assistant').count(), 1)
  assert.equal(await page.locator('.message.assistant').getByText(/等待中的任务/).count(), 0)
  report.checks.push('new_task_cancels_pending_stream')
  await submit('身份核验')
  await page.getByRole('link', { name: '测试学者教师简介' }).waitFor()
  assert.equal(await page.getByRole('link', { name: '测试学者教师简介' }).getAttribute('href'), 'https://example.edu.cn/profile')
  await page.getByText('此页仅核验任职关联，观点以本条正文为准。', { exact: true }).waitFor()
  report.checks.push('independent_identity_source_link')
  await page.screenshot({ path: `${outputDir}/desktop.png`, fullPage: true })
  await page.getByRole('button', { name: '本地专家库', exact: true }).click()
  await page.getByRole('heading', { name: '本地专家库', exact: true }).waitFor()
  await page.getByRole('button', { name: books[1].title, exact: true }).click()
  await page.getByRole('textbox', { name: '检索教材' }).fill('书内 397')
  await page.getByRole('button', { name: '检索教材', exact: true }).click()
  await page.locator('.textbook-location').getByText('书内 p397（PDF p418）', { exact: true }).waitFor()
  await page.locator('.source-metadata').getByText('出版年份', { exact: true }).waitFor()
  assert.equal(await page.locator('.source-metadata').getByText('施行日期', { exact: true }).count(), 0)
  assert.deepEqual(libraryRequests.at(-1), { bookId: books[1].book_id, query: '书内 397' })
  assert.equal(await page.getByRole('button', { name: '民法典', exact: true }).count(), 0)
  await page.screenshot({ path: `${outputDir}/expert-library.png`, fullPage: true })
  report.checks.push('expert_library_lists_and_searches_imported_textbooks')
  const mobile = await context.newPage()
  mobile.on('pageerror', (error) => errors.push(error.message))
  await mobile.setViewportSize({ width: 375, height: 812 })
  await mobile.goto(`http://127.0.0.1:${port}`)
  await submit('手机验证', mobile)
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
  await mobile.screenshot({ path: `${outputDir}/mobile.png`, fullPage: true })
  report.checks.push('mobile_question_and_layout')
  await mobile.getByRole('button', { name: '本地专家库', exact: true }).click()
  await mobile.getByRole('heading', { name: books[0].title, exact: true }).waitFor()
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false)
  await mobile.screenshot({ path: `${outputDir}/expert-library-mobile.png`, fullPage: true })
  report.checks.push('expert_library_mobile_layout')
  assert.deepEqual(errors, [])
  report.status = 'passed'
  console.log(`PASS: ${report.checks.length} browser acceptance checks`)
} catch (error) {
  report.status = 'failed'
  report.error = error.stack || String(error)
  await page?.screenshot({ path: `${outputDir}/failure.png`, fullPage: true }).catch(() => {})
  throw error
} finally {
  for (const resolve of gates.values()) resolve()
  try {
    await context?.tracing.stop({ path: `${outputDir}/trace.zip` })
  } catch (error) {
    report.trace_error = String(error)
    report.status = 'failed'
    process.exitCode = 1
  }
  await browser?.close()
  vite.kill()
  report.finished_at = new Date().toISOString()
  await writeFile(`${outputDir}/report.json`, JSON.stringify(report, null, 2))
  await writeFile(`${outputDir}/vite.txt`, logs)
}
