import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import { CheckCircle2, Database, KeyRound, X, XCircle } from 'lucide-react'
import type { Health, ProviderStatus } from '@/types/legal'

interface ModelDialogProps {
  health?: Health
  provider?: ProviderStatus
  onClose: () => void
}

interface Offset {
  x: number
  y: number
}

const EDGE_MARGIN = 8
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

export function ModelDialog({ health, provider, onClose }: ModelDialogProps) {
  const dialogRef = useRef<HTMLElement>(null)
  const dragState = useRef<{
    pointerId: number
    startX: number
    startY: number
    origin: Offset
    rect: DOMRect
  } | undefined>(undefined)
  const [offset, setOffset] = useState<Offset>({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  useEffect(() => {
    const keepInViewport = () => setOffset((current) => {
      const rect = dialogRef.current?.getBoundingClientRect()
      if (!rect) return current
      return {
        x: clamp(current.x, current.x + EDGE_MARGIN - rect.left, current.x + window.innerWidth - EDGE_MARGIN - rect.right),
        y: clamp(current.y, current.y + EDGE_MARGIN - rect.top, current.y + window.innerHeight - EDGE_MARGIN - rect.bottom),
      }
    })
    window.addEventListener('resize', keepInViewport)
    return () => window.removeEventListener('resize', keepInViewport)
  }, [])

  function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = dialogRef.current?.getBoundingClientRect()
    if (event.button !== 0 || !rect) return
    dragState.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: offset,
      rect,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    event.currentTarget.focus()
    setDragging(true)
    event.preventDefault()
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragState.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const next = {
      x: drag.origin.x + event.clientX - drag.startX,
      y: drag.origin.y + event.clientY - drag.startY,
    }
    setOffset({
      x: clamp(next.x, drag.origin.x + EDGE_MARGIN - drag.rect.left, drag.origin.x + window.innerWidth - EDGE_MARGIN - drag.rect.right),
      y: clamp(next.y, drag.origin.y + EDGE_MARGIN - drag.rect.top, drag.origin.y + window.innerHeight - EDGE_MARGIN - drag.rect.bottom),
    })
  }

  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragState.current?.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    dragState.current = undefined
    setDragging(false)
  }

  function moveWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 40 : 10
    const deltas: Record<string, Offset> = {
      ArrowLeft: { x: -step, y: 0 },
      ArrowRight: { x: step, y: 0 },
      ArrowUp: { x: 0, y: -step },
      ArrowDown: { x: 0, y: step },
    }
    const delta = deltas[event.key]
    if (!delta) return
    event.preventDefault()
    setOffset((current) => {
      const rect = dialogRef.current?.getBoundingClientRect()
      if (!rect) return current
      const next = { x: current.x + delta.x, y: current.y + delta.y }
      return {
        x: clamp(next.x, current.x + EDGE_MARGIN - rect.left, current.x + window.innerWidth - EDGE_MARGIN - rect.right),
        y: clamp(next.y, current.y + EDGE_MARGIN - rect.top, current.y + window.innerHeight - EDGE_MARGIN - rect.bottom),
      }
    })
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section
        ref={dialogRef}
        className={`model-dialog ${dragging ? 'is-dragging' : ''}`}
        style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-dialog-title"
      >
        <button type="button" className="icon-button dialog-close" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        <div
          className="dialog-drag-handle"
          role="button"
          tabIndex={0}
          aria-label="移动弹窗，按住拖动或使用方向键"
          aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight"
          title="按住拖动；聚焦后可用方向键移动"
          onPointerDown={startDrag}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onLostPointerCapture={() => { dragState.current = undefined; setDragging(false) }}
          onKeyDown={moveWithKeyboard}
        >
          <KeyRound size={25} />
          <h2 id="model-dialog-title">模型与数据边界</h2>
        </div>
        <div className={`model-connection ${health?.model_configured ? 'is-connected' : ''}`}>
          {health?.model_configured ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
          <div>
            <strong>{health?.model_configured ? `${health.model} 已连接` : '当前为原文检索模式'}</strong>
            <p>{health?.model_configured ? '回答生成后会经过独立引用校验。' : '未配置 API Key，系统不会模拟或伪造模型回答。'}</p>
          </div>
        </div>
        <h3>DeepSeek 配置</h3>
        <p>在项目根目录的 `.env` 中设置后重新启动：</p>
        <code>DEEPSEEK_API_KEY=你的密钥</code>
        <div className="provider-dialog-section">
          <h3><Database size={16} />北大法宝 MCP</h3>
          <div className={`model-connection ${provider?.configured ? 'is-connected' : ''}`}>
            {provider?.configured ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
            <div>
              <strong>{provider?.configured ? 'Token 已配置，等待真实检索验证' : '尚未配置 Token'}</strong>
              <p>{provider?.strategy || '正在读取数据源能力。'}</p>
            </div>
          </div>
          <p>在同一 `.env` 文件中填写 Token 本身，不要带 `Bearer` 前缀：</p>
          <code>PKULAW_AUTH_TOKEN=你的Access Token</code>
          <p className="dialog-note">相同查询短时缓存，避免重复扣积分。状态中的“已配置”不代表在线调用已验证；密钥只由本地后端读取。</p>
        </div>
        <button type="button" className="primary-button dialog-confirm" onClick={onClose}>知道了</button>
      </section>
    </div>
  )
}
