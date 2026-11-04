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
  const page = await context.newPage()
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', (error) => consoleErrors.push(error.message))
  page.on('response', (response) => { if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`) })
  page.on('requestfailed', (request) => failedRequests.push(`${request.failure()?.errorText} ${request.url()}`))

  await page.goto('http://127.0.0.1:7100', { waitUntil: 'networkidle' })
  await page.getByRole('heading', { name: '法律文献证据问答', exact: true }).waitFor()
  await page.screenshot({ path: `${outputDir}/desktop-empty.png`, fullPage: true })

  await page.getByRole('button', { name: /精确读取/ }).click()
  await page.getByText('已定位到右侧所列官方原文。', { exact: false }).waitFor({ timeout: 15000 })
  await page.locator('.evidence-item').first().getByText('第一千一百六十五条', { exact: true }).waitFor()
  const evidenceCount = await page.locator('.evidence-item').count()
  await page.screenshot({ path: `${outputDir}/desktop-answer.png`, fullPage: true })

  await page.getByRole('button', { name: /复制原文/ }).click()
  await page.getByText('原文与来源已复制', { exact: true }).waitFor()
  await page.getByRole('button', { name: /原文检索模式|deepseek/ }).click()
  await page.getByRole('dialog').waitFor()
  await page.getByRole('button', { name: '知道了' }).click()

  await page.getByRole('button', { name: '法律文库', exact: true }).click()
  await page.getByRole('textbox', { name: '检索法律文库' }).fill('离婚冷静期')
  await page.getByRole('button', { name: '检索原文' }).click()
  await page.locator('.evidence-item').first().getByText('第一千零七十七条', { exact: true }).waitFor({ timeout: 15000 })
  await page.screenshot({ path: `${outputDir}/desktop-library.png`, fullPage: true })

  const mobile = await context.newPage()
  mobile.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`mobile: ${message.text()}`) })
  mobile.on('pageerror', (error) => consoleErrors.push(`mobile: ${error.message}`))
  await mobile.setViewportSize({ width: 375, height: 812 })
  await mobile.goto('http://127.0.0.1:7100', { waitUntil: 'networkidle' })
  await mobile.screenshot({ path: `${outputDir}/mobile-empty.png`, fullPage: true })
  await mobile.getByRole('textbox', { name: '输入法律任务' }).fill('第1165条原文')
  await mobile.getByRole('button', { name: '发送任务' }).click()
  await mobile.locator('.evidence-item').first().getByText('第一千一百六十五条', { exact: true }).waitFor({ timeout: 15000 })
  await mobile.screenshot({ path: `${outputDir}/mobile-answer.png`, fullPage: true })
  const mobileOverflow = await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  const requestCount = await page.evaluate(() => performance.getEntriesByType('resource').length)

  const result = {
    health,
    evidenceCount,
    mobileOverflow,
    consoleErrors,
    failedRequests,
    requestCount,
    screenshots: ['desktop-empty.png', 'desktop-answer.png', 'desktop-library.png', 'mobile-empty.png', 'mobile-answer.png'],
  }
  console.log(JSON.stringify(result, null, 2))
  if (consoleErrors.length || failedRequests.length || mobileOverflow || evidenceCount !== 1) process.exitCode = 1
} catch (error) {
  console.error(error)
  console.error(serverLogs.join(''))
  process.exitCode = 1
} finally {
  if (browser) await browser.close()
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(server.pid), '/T', '/F'], { stdio: 'ignore' })
  else server.kill('SIGTERM')
}
