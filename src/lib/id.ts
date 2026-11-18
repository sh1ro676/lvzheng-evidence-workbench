/** 生成本地唯一 ID。
 *
 * crypto.randomUUID 只在安全上下文（https / localhost）可用；
 * 通过 http://<公网IP> 访问时它是 undefined，会让提交动作直接抛错。
 * 这里优先用 randomUUID，退化到 getRandomValues，再退化到时间戳。
 */
export function newId(): string {
  const cryptoObj = globalThis.crypto as Crypto | undefined
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID()
  if (cryptoObj?.getRandomValues) {
    const bytes = cryptoObj.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
