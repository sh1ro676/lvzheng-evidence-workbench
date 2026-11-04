import { useEffect } from 'react'
import { CheckCircle2, KeyRound, X, XCircle } from 'lucide-react'
import type { Health } from '@/types/legal'

interface ModelDialogProps {
  health?: Health
  onClose: () => void
}

export function ModelDialog({ health, onClose }: ModelDialogProps) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-dialog-title">
        <button type="button" className="icon-button dialog-close" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        <KeyRound size={25} />
        <h2 id="model-dialog-title">模型与数据边界</h2>
        <div className={`model-connection ${health?.model_configured ? 'is-connected' : ''}`}>
          {health?.model_configured ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
          <div>
            <strong>{health?.model_configured ? `${health.model} 已连接` : '当前为原文检索模式'}</strong>
            <p>{health?.model_configured ? '回答生成后会经过独立引用校验。' : '未配置 API Key，系统不会模拟或伪造模型回答。'}</p>
          </div>
        </div>
        <h3>启用 DeepSeek</h3>
        <p>在项目目录设置环境变量后重新启动开发服务：</p>
        <code>DEEPSEEK_API_KEY=你的密钥</code>
        <p className="dialog-note">密钥仅由本地后端读取，不写入前端代码。法律检索、原文查看和条号定位无需密钥。</p>
        <button type="button" className="primary-button dialog-confirm" onClick={onClose}>知道了</button>
      </section>
    </div>
  )
}
