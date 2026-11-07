import { useEffect } from 'react'
import { CheckCircle2, Database, KeyRound, X, XCircle } from 'lucide-react'
import type { Health, ProviderStatus } from '@/types/legal'

interface ModelDialogProps {
  health?: Health
  provider?: ProviderStatus
  onClose: () => void
}

export function ModelDialog({ health, provider, onClose }: ModelDialogProps) {
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
