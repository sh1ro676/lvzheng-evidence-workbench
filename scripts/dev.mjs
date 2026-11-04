import { spawn } from 'node:child_process'

const args = process.argv.slice(2)
const python = process.env.PYTHON || 'python'
const backend = spawn(python, ['backend/server.py'], { stdio: 'inherit', env: process.env })
const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', ...args], { stdio: 'inherit', env: process.env })
let stopping = false

function stop(code = 0) {
  if (stopping) return
  stopping = true
  backend.kill('SIGTERM')
  vite.kill('SIGTERM')
  setTimeout(() => process.exit(code), 150).unref()
}

backend.on('exit', (code) => { if (!stopping && code) stop(code) })
vite.on('exit', (code) => stop(code || 0))
process.on('SIGINT', () => stop(0))
process.on('SIGTERM', () => stop(0))
