/**
 * 文本清洗 — 剥离协作回复中的系统痕迹
 *
 * 目标（#24）：协作工作台回传给前端的回复只保留自然语言文本，
 * 过滤掉 thinking / <tool_call> / <parameter> / <result> 等系统痕迹。
 *
 * - StreamingTextSanitizer：流式处理，跨 text_delta 边界正确剥离块级系统标签。
 * - sanitizeAssistantText：对完整文本做一次性清理（用于消息历史 / 预览）。
 */

/** 需要整体剥离内容块的系统标签名（小写） */
const SYSTEM_BLOCK_TAGS = [
  'thinking',
  'tool_call',
  'toolcall',
  'parameter',
  'result',
  'query',
  'search',
  'reasoning',
  'thought',
  'scratchpad',
] as const

/** 判断一个标签名是否属于系统痕迹块 */
function isSystemBlock(tagName: string): boolean {
  return (SYSTEM_BLOCK_TAGS as readonly string[]).includes(tagName)
}

/**
 * 流式文本清洗器
 *
 * 逐段累加原始文本，跨 delta 边界剥离 thinking / <tool_call> / [thinking] 等块级系统标签，
 * 只输出自然语言增量。若缓冲区末尾是不完整的标签，则暂缓输出，等待后续增量补齐。
 */
export class StreamingTextSanitizer {
  private buffer = ''
  /** 当前处于哪些未闭合的系统块内（栈） */
  private stack: string[] = []

  /** 追加一段原始文本增量，返回本次应回传给前端的自然语言增量 */
  push(chunk: string): string {
    this.buffer += chunk
    return this._extract(false)
  }

  /** 终止流，返回剩余可回传的文本（容错剥离未闭合的系统块） */
  flush(): string {
    const out = this._extract(true)
    this.buffer = ''
    this.stack = []
    return out
  }

  /**
   * 计算当前可安全处理的上界：若末尾存在不完整的标签（< 或 [ 其后没有配对 > 或 ]），
   * 则停在该标签之前，将其保留在缓冲区等待后续增量。
   */
  private _safeLimit(force: boolean): number {
    if (force) return this.buffer.length
    const lastOpen = Math.max(this.buffer.lastIndexOf('<'), this.buffer.lastIndexOf('['))
    const lastClose = Math.max(this.buffer.lastIndexOf('>'), this.buffer.lastIndexOf(']'))
    return lastOpen > lastClose ? lastOpen : this.buffer.length
  }

  private _extract(force: boolean): string {
    const limit = this._safeLimit(force)
    let out = ''
    let i = 0
    while (i < limit) {
      const ch = this.buffer[i]
      const isTagDelim = ch === '<' || ch === '['
      if (!isTagDelim) {
        if (this.stack.length === 0) out += ch
        i++
        continue
      }

      const closeDelim = ch === '<' ? '>' : ']'
      const tagEnd = this.buffer.indexOf(closeDelim, i)
      if (tagEnd === -1 || tagEnd >= limit) {
        if (force) {
          // 无更多输入：把悬空的 < 或 [ 当作字面文本，避免吞掉正文
          if (this.stack.length === 0) out += ch
          i++
          continue
        }
        // 保留尾部不完整标签，等待后续输入补齐
        break
      }

      const inner = this.buffer.slice(i + 1, tagEnd)
      const trimmed = inner.trim()
      // 只有形如标签的内容（字母开头，或以 / 开头的闭合标签）才按标签处理，
      // 避免把正文中的 “<”/“[” 误判为标签（如 “a < b”、“[1]”）。
      if (!/^[a-zA-Z]/.test(trimmed) && !trimmed.startsWith('/')) {
        if (this.stack.length === 0) out += ch
        i++
        continue
      }

      const isClose = trimmed.charAt(0) === '/'
      const name = (isClose ? trimmed.slice(1) : trimmed).split(/[\s=/]/)[0].toLowerCase()

      if (isClose) {
        const idx = this.stack.lastIndexOf(name)
        if (idx !== -1) this.stack.length = idx
      } else if (isSystemBlock(name)) {
        this.stack.push(name)
      }
      // 其它普通标签（如 <b>）不属于系统块：不进入栈，也不回传标签标记本身
      i = tagEnd + 1
    }

    this.buffer = this.buffer.slice(i)
    return out
  }
}

/**
 * 对完整文本做一次性清理，剥离系统痕迹标签与标记。
 * 用于消息历史、预览等非流式场景。
 */
export function sanitizeAssistantText(text: string): string {
  if (!text) return ''
  let out = text

  // 成对出现的系统块（含可选的参数部分，如 <tool_call name="x">）
  for (const tag of SYSTEM_BLOCK_TAGS) {
    const pairRe = new RegExp(`<${tag}(?:\\s[^>]*)?>[\\s\\S]*?<\\/${tag}\\s*>`, 'gi')
    out = out.replace(pairRe, '')
    // 残留的单独起始/结束标签
    const anyRe = new RegExp(`</?${tag}(?:\\s[^>]*)?>`, 'gi')
    out = out.replace(anyRe, '')
  }

  // 兼容 [thinking]...[/thinking] 这类方括号标记
  out = out.replace(
    /\[\s*(thinking|tool_call|toolcall|parameter|result|query|search|reasoning)\s*\][\s\S]*?\[\s*\/\s*\1\s*\]/gi,
    '',
  )

  // 清理多余空行与首尾空白
  out = out.replace(/[ \t]+\r?\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
  return out
}