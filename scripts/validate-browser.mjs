import { spawn, spawnSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const outputDir = fileURLToPath(new URL('../.validation/', import.meta.url))
await mkdir(outputDir, { recursive: true })
const server = spawn(process.execPath, ['scripts/dev.mjs', '--host', '127.0.0.1', '--port', '7100'], {
  stdio: ['ignore', 'pipe', 'pipe'], env: process.env,
})
let browser
const consoleErrors = []
const failedRequests = []
const serverLogs = []
server.stdout.on('data', (chunk) => serverLogs.push(chunk.toString()))
server.stderr.on('data', (chunk) => serverLogs.push(chunk.toString()))

async function waitForServer() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch('http://127.0.0.1:7100/api/health')
      if (response.ok) return response.json()
    } catch { /* server is still starting */ }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`服务启动超时\n${serverLogs.join('')}`)
}

try {
  const health = await waitForServer()
  browser = await chromium.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    headless: true,
  })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] })
  context.setDefaultTimeout(60000)
  context.setDefaultNavigationTimeout(90000)
  const page = await context.newPage()
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', (error) => consoleErrors.push(error.message))
  page.on('response', (response) => { if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`) })
  page.on('requestfailed', (request) => failedRequests.push(`${request.failure()?.errorText} ${request.url()}`))

  await page.goto('http://127.0.0.1:7100', { waitUntil: 'domcontentloaded' })
  await page.getByRole('heading', { name: '法律文献证据问答', exact: true }).waitFor()
  await page.screenshot({ path: `${outputDir}/desktop-empty.png`, fullPage: true })

  // Deterministic regression tests do not spend paid web-search requests.
  await page.getByRole('button', { name: /联网增强/ }).click()
  await page.getByRole('button', { name: /精确读取/ }).click()
  await page.locator('.message.assistant .answer-status').waitFor({ timeout: 30000 })
  await page.locator('.evidence-item').first().getByText('第一千一百六十五条', { exact: true }).waitFor()
  const evidenceCount = await page.locator('.evidence-item').count()
  await page.screenshot({ path: `${outputDir}/desktop-answer.png`, fullPage: true })

  await page.getByRole('button', { name: /复制原文/ }).click()
  await page.getByText('原文与来源已复制', { exact: true }).waitFor()
  await page.getByRole('button', { name: /原文检索模式|deepseek/ }).click()
  await page.getByRole('dialog').waitFor()
  await page.getByText('DeepSeek 联网搜索', { exact: true }).waitFor()
  await page.getByRole('button', { name: '知道了' }).click()

  await page.getByRole('button', { name: '法律文库', exact: true }).click()
  const documentTabCount = await page.locator('.document-tab').count()
  if (documentTabCount !== 6) throw new Error(`文库应展示六部法律，实际为 ${documentTabCount}`)
  await page.getByRole('button', { name: '刑法', exact: true }).click()
  await page.locator('.document-heading-copy h1').getByText('中华人民共和国刑法', { exact: true }).waitFor()
  await page.getByRole('textbox', { name: '检索法律文库' }).fill('第 1 条')
  await page.getByRole('button', { name: '检索原文' }).click()
  await page.locator('.evidence-item').first().getByText('第一条', { exact: true }).waitFor({ timeout: 15000 })
  await page.locator('.evidence-item').first().getByText(/中华人民共和国刑法/).waitFor()
  await page.getByRole('button', { name: '民法典', exact: true }).click()
  await page.locator('.document-heading-copy h1').getByText('中华人民共和国民法典', { exact: true }).waitFor()
  await page.getByRole('textbox', { name: '检索法律文库' }).fill('离婚冷静期')
  await page.getByRole('button', { name: '检索原文' }).click()
  await page.locator('.evidence-item').first().getByText('第一千零七十七条', { exact: true }).waitFor({ timeout: 15000 })
  await page.screenshot({ path: `${outputDir}/desktop-library.png`, fullPage: true })

  const mobile = await context.newPage()
  mobile.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`mobile: ${message.text()}`) })
  mobile.on('pageerror', (error) => consoleErrors.push(`mobile: ${error.message}`))
  await mobile.setViewportSize({ width: 375, height: 812 })
  await mobile.goto('http://127.0.0.1:7100', { waitUntil: 'domcontentloaded' })
  await mobile.screenshot({ path: `${outputDir}/mobile-empty.png`, fullPage: true })
  await mobile.getByRole('button', { name: /联网增强/ }).click()
  await mobile.getByRole('textbox', { name: '输入法律任务' }).fill('第1165条原文')
  await mobile.getByRole('button', { name: '发送任务' }).click()
  await mobile.locator('.evidence-item').first().getByText('第一千一百六十五条', { exact: true }).waitFor({ timeout: 15000 })
  await mobile.screenshot({ path: `${outputDir}/mobile-answer.png`, fullPage: true })
  await mobile.getByRole('button', { name: '文库', exact: true }).click()
  if (await mobile.locator('.document-tab').count() !== 6) throw new Error('手机文库未展示六部法律')
  await mobile.getByRole('button', { name: '刑诉法解释', exact: true }).click()
  await mobile.locator('.document-heading-copy h1').getByText('最高人民法院关于适用《中华人民共和国刑事诉讼法》的解释', { exact: true }).waitFor()
  await mobile.screenshot({ path: `${outputDir}/mobile-library.png`, fullPage: true })
  const mobileOverflow = await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)

  const structured = await context.newPage()
  structured.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`structured: ${message.text()}`) })
  structured.on('pageerror', (error) => consoleErrors.push(`structured: ${error.message}`))
  const structuredEvidence = [
    { citation_id: 1, document_id: 'civil-code', article_id: '8', document_title: '中华人民共和国民法典', article_label: '第八条', section_path: ['第一编', '第一章'], text: '民事主体从事民事活动，不得违反法律，不得违背公序良俗。', content_hash: 'test', source_name: '国家法律法规数据库', source_url: 'https://flk.npc.gov.cn/', snapshot_sha256: 'test', published_at: '2020-05-28', effective_at: '2021-01-01', status: '现行有效', source_notice: '测试数据', match_type: 'exact', relevance: 1 },
    { citation_id: 2, document_id: 'web-1', article_id: 'web-1', document_title: '公序良俗原则的适用研究', article_label: '网页正文', section_path: ['中国政法大学', '四校定向检索'], text: '中国政法大学易军教授在正文中分析公序良俗原则，并直接讨论民法典第八条。', content_hash: 'test', provider: 'deepseek_web', source_name: '公开论文网页', source_url: 'https://example.com/professor', snapshot_sha256: 'test', published_at: '', effective_at: '', status: '公开网页', source_notice: '测试数据', match_type: 'web', relevance: 1, institution: '中国政法大学', relevance_basis: 'article_text' },
    { citation_id: 3, document_id: 'web-2', article_id: 'web-2', document_title: '民事活动基本原则研究', article_label: '主题相关论文网页', section_path: ['北京大学法学院', '四校定向检索'], text: '北京大学法学院尹田教授从基本原则角度讨论民事活动的规范边界。', content_hash: 'test', provider: 'deepseek_web', source_name: '公开论文网页', source_url: 'https://example.com/topic-paper', snapshot_sha256: 'test', published_at: '', effective_at: '', status: '公开网页', source_notice: '测试数据', match_type: 'web', relevance: 0.82, institution: '北京大学法学院', relevance_basis: 'topic_paper' },
  ]
  const schoolWebSearch = {
    enabled: true, provider: 'deepseek_web', configured: true, mode: 'four_institutions',
    queries: ['民法典 第八条 中国政法大学 教授 论文', '公序良俗 中国政法大学 教授 论文'],
    search_count: 8, candidate_count: 12, fetch_attempt_count: 8, content_fetched_count: 5,
    rejected_count: 10, fetched_count: 2, queried_at: '2026-11-14T00:00:00Z', fallback_general: false,
    institution_results: [
      { institution: '中国政法大学', found: true, search_count: 1, candidate_count: 3, content_fetched_count: 2, rejected_count: 2 },
      { institution: '西北政法大学', found: false, search_count: 3, candidate_count: 3, content_fetched_count: 1, rejected_count: 3, reason: '未获得可核验论文' },
      { institution: '北京大学法学院', found: true, search_count: 2, candidate_count: 4, content_fetched_count: 1, rejected_count: 3 },
      { institution: '清华大学法学院', found: false, search_count: 2, candidate_count: 2, content_fetched_count: 1, rejected_count: 2, reason: '未获得可核验论文' },
    ],
    events: [
      { stage: 'search', ok: true, query: '民法典 第八条 中国政法大学 教授 论文', result_count: 1, institution: '中国政法大学' },
      { stage: 'candidate', ok: true, query: '民法典 第八条 中国政法大学 教授 论文', title: '无关百科', url: 'https://example.com/biography', institution: '中国政法大学' },
      { stage: 'retry', ok: true, query: '公序良俗 中国政法大学 教授 论文', reason: '上一轮未获得有效证据，换用主题词或条号变体', institution: '中国政法大学' },
      { stage: 'candidate', ok: true, query: '公序良俗 中国政法大学 教授 论文', title: '论文示例', url: 'https://example.com/professor', institution: '中国政法大学' },
      { stage: 'fetch', ok: true, query: '公序良俗 中国政法大学 教授 论文', url: 'https://example.com/professor', institution: '中国政法大学' },
      { stage: 'filter', ok: false, query: '民法典 第八条 中国政法大学 教授 论文', url: 'https://example.com/biography', reason: '网页正文未明确提及所问法条', institution: '中国政法大学' },
      { stage: 'evidence', ok: true, query: '公序良俗 中国政法大学 教授 论文', url: 'https://example.com/professor', institution: '中国政法大学' },
    ],
  }
  await structured.route('**/api/chat/stream', (route) => {
    const firstAnswer = '## 一、法条原文\n民法典第八条规定民事主体不得违反法律、不得违背公序良俗。【证据1】\n## 三、理解与适用要点\n模型可以结合**一般法律知识**继续分析，不要求每句话都有引用。'
    const final = {
      done: true, status: 'verified', answer: firstAnswer, followup: 'schools',
      evidence: [structuredEvidence[0]], verification: { passed: true, checks: [] },
      retrieval: { source: 'pkulaw', provider: 'pkulaw' },
    }
    return route.fulfill({
      status: 200, contentType: 'text/event-stream; charset=utf-8',
      body: `data: ${JSON.stringify({ stage: 'planning', elapsed_ms: 0 })}\n\ndata: ${JSON.stringify({ stage: 'generating', elapsed_ms: 12 })}\n\ndata: ${JSON.stringify({ delta: firstAnswer })}\n\ndata: ${JSON.stringify(final)}\n\n`,
    })
  })
  await structured.route('**/api/schools', (route) => route.fulfill({
    status: 200, contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({
      status: 'verified', replace: false, section_title: '四校教授观点',
      section: '中国政法大学方面，易军教授结合民法典第八条分析公序良俗原则的适用边界。【证据1】\n（二）北京大学尹田教授\n尹田教授从民事活动基本原则出发讨论规范边界，可作为主题研究资料。【证据2】',
      evidence: [structuredEvidence[1], structuredEvidence[2]].map((item, index) => ({ ...item, citation_id: index + 1 })),
      retrieval: { web_search: schoolWebSearch },
    }),
  }))
  await structured.goto('http://127.0.0.1:7100', { waitUntil: 'domcontentloaded' })
  await structured.getByRole('textbox', { name: '输入法律任务' }).fill('民法典第八条，需要四校高校老师的讲解')
  await structured.getByRole('button', { name: '发送任务' }).click()
  await structured.locator('.school-opinion').first().waitFor()
  const headings = await structured.locator('.answer-heading').allTextContents()
  if (headings.join('|') !== '一、法条原文|三、理解与适用要点|四校教授观点') throw new Error(`分节标题渲染异常：${headings.join('|')}`)
  const schoolNames = await structured.locator('.school-name').allTextContents()
  if (schoolNames.join('|') !== '中国政法大学|西北政法大学|北京大学法学院|清华大学法学院') throw new Error(`学校分区或顺序异常：${schoolNames.join('|')}`)
  await structured.locator('.school-opinion').nth(0).getByText(/易军教授结合民法典第八条/).waitFor()
  await structured.locator('.school-opinion').nth(0).getByText('正文提及所问条号', { exact: true }).waitFor()
  await structured.locator('.school-opinion').nth(1).getByText(/本次检索未获得该校教授/).waitFor()
  await structured.locator('.school-opinion').nth(2).getByText('尹田教授', { exact: true }).waitFor()
  await structured.locator('.school-opinion').nth(2).getByText(/主题相关论文未逐字引用所问条号/).waitFor()
  await structured.locator('.school-opinion').nth(3).getByText(/本次检索未获得该校教授/).waitFor()
  await structured.getByRole('button', { name: '查看证据 2', exact: true }).first().click()
  await structured.locator('.evidence-item.is-selected').getByText('网页正文', { exact: true }).waitFor()
  await structured.locator('.web-search-details summary').click()
  await structured.locator('.web-search-details').getByText(/换词重试/).waitFor()
  await structured.locator('.web-search-details').getByText(/论文示例/).waitFor()
  await structured.locator('.web-search-details').getByText(/网页正文未明确提及所问法条/).waitFor()
  const responsiveChecks = []
  for (const width of [1440, 1024, 768, 375]) {
    await structured.setViewportSize({ width, height: 900 })
    await structured.waitForTimeout(120)
    const overflow = await structured.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    responsiveChecks.push({ width, overflow })
    if (width === 1440) await structured.screenshot({ path: `${outputDir}/desktop-sections.png`, fullPage: true })
    if (width === 375) await structured.screenshot({ path: `${outputDir}/mobile-sections.png`, fullPage: true })
  }
  const textbookPage = await context.newPage()
  textbookPage.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`textbook: ${message.text()}`) })
  textbookPage.on('pageerror', (error) => consoleErrors.push(`textbook: ${error.message}`))
  textbookPage.on('response', (response) => { if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`) })
  textbookPage.on('requestfailed', (request) => failedRequests.push(`${request.failure()?.errorText} ${request.url()}`))
  const textbookText = '正当防卫的成立必须存在现实的不法侵害，防卫行为应当针对正在进行的不法侵害。\n防卫明显超过必要限度造成重大损害的，应当负刑事责任，但是应当减轻或者免除处罚。这一段用于验证完整教材原文展开。'
  const textbookEvidence = {
    citation_id: 2, document_id: 'book-0', article_id: 'p418', document_title: '刑法学（第六版）· 上册',
    article_label: '书内 p397（PDF p418）', section_path: ['刑法学（第六版）· 上册', '书内 p397'], text: textbookText,
    content_hash: 'textbook-test', provider: 'textbook', source_name: '张明楷《刑法学》（第六版）· 法律出版社', source_url: '',
    snapshot_sha256: 'textbook-test', published_at: '2021', effective_at: '', status: '教材原文（OCR，可能有个别错字，引用前请核对纸书）',
    source_notice: '该证据由 OCR 提取，可能存在错字或标点误差；对外引用前须与纸书第对应页核对。', match_type: 'textbook', relevance: 1,
  }
  await textbookPage.route('**/api/chat/stream', (route) => {
    const answer = '## 一、法条原文\n民法典第八条规定民事主体不得违反法律、不得违背公序良俗。【证据1】\n## 二、教材讲评\n教材围绕正当防卫的成立条件与防卫限度展开分析。【证据2】'
    const final = {
      done: true, status: 'verified', answer, followup: null,
      evidence: [structuredEvidence[0], textbookEvidence], verification: { passed: true, checks: [] },
      retrieval: { source: 'pkulaw', provider: 'pkulaw' },
    }
    return route.fulfill({
      status: 200, contentType: 'text/event-stream; charset=utf-8',
      body: `data: ${JSON.stringify({ delta: answer })}\n\ndata: ${JSON.stringify(final)}\n\n`,
    })
  })
  await textbookPage.goto('http://127.0.0.1:7100', { waitUntil: 'domcontentloaded' })
  await textbookPage.getByRole('textbox', { name: '输入法律任务' }).fill('请结合教材讲解正当防卫')
  await textbookPage.getByRole('button', { name: '发送任务' }).click()
  await textbookPage.getByRole('button', { name: '查看证据 2', exact: true }).click()
  const textbookCard = textbookPage.locator('#evidence-2')
  await textbookCard.locator('.textbook-location').waitFor()
  await textbookCard.locator('.textbook-location').getByText('刑法学（第六版）· 上册', { exact: true }).waitFor()
  await textbookCard.locator('.textbook-location').getByText('书内 p397（PDF p418）', { exact: true }).waitFor()
  await textbookCard.locator('.legal-original').getByText(/这一段用于验证完整教材原文展开。/).waitFor()
  if (await textbookCard.locator('.legal-original.is-preview').count()) throw new Error('点击教材引用后原文仍处于折叠状态')
  if (await textbookPage.locator('.evidence-item.is-selected').count() !== 1) throw new Error('教材引用应只选中一张证据卡片')
  if (!(await textbookCard.evaluate((element) => element.classList.contains('is-selected')))) throw new Error('教材证据卡片未被选中')
  await textbookPage.screenshot({ path: `${outputDir}/desktop-textbook-location.png`, fullPage: true })
  await textbookPage.setViewportSize({ width: 375, height: 812 })
  await textbookPage.waitForTimeout(120)
  const textbookOverflow = await textbookPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  await textbookPage.screenshot({ path: `${outputDir}/mobile-textbook-location.png`, fullPage: true })

  const requestCount = await page.evaluate(() => performance.getEntriesByType('resource').length)

  const result = {
    health,
    evidenceCount,
    mobileOverflow,
    textbookOverflow,
    responsiveChecks,
    consoleErrors,
    failedRequests,
    requestCount,
    screenshots: ['desktop-empty.png', 'desktop-answer.png', 'desktop-library.png', 'mobile-empty.png', 'mobile-answer.png', 'desktop-sections.png', 'mobile-sections.png', 'desktop-textbook-location.png', 'mobile-textbook-location.png'],
  }
  console.log(JSON.stringify(result, null, 2))
  if (consoleErrors.length || failedRequests.length || mobileOverflow || textbookOverflow || responsiveChecks.some((check) => check.overflow) || evidenceCount !== 1) process.exitCode = 1
} catch (error) {
  console.error(error)
  console.error(serverLogs.join(''))
  process.exitCode = 1
} finally {
  if (browser) await browser.close()
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(server.pid), '/T', '/F'], { stdio: 'ignore' })
  else server.kill('SIGTERM')
}
