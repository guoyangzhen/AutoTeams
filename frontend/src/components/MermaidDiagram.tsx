/**
 * MermaidDiagram — 通用 mermaid 图渲染封装（#16 / #13 共用）。
 *
 * 特性：
 * - 用 mermaid 客户端 API 渲染，区别于 `<pre class="mermaid">` 的自动扫描；
 * - 随容器 CSS 语义变量（--bg-surface / --text-primary / --brand / --border-strong）
 *   自动适配浅色纸感区与深空仪表区，无需手动切换主题；
 * - 内置缩放 / 复位 / 适应宽度控制，默认按容器宽度自适应（清晰可读）；
 * - 数据变化时自动重渲染（props.chart 变更即触发），随 runtime 数据更新。
 *
 * 稳定性要点（修复 #2 / #6）：
 * - mermaid 的 render 结果与临时节点（div / svg / style）由 mermaid 注入 body，
 *   若解析失败会在页面底部留下「Syntax error in text」大段报错块。这里在成功与
 *   失败路径上都会清理本组件 baseId 相关的所有注入节点，避免跨路由残留。
 * - mermaid v11 偶发「永久挂起」（render Promise 永不 resolve），且并发调用会
 *   损坏其内部状态。这里用「全局串行队列」保证同一时刻只有一个 render 在跑，
 *   并用 Promise.race 超时兜底，避免首帧空白与整图渲染失败。
 * - 使用 SVG 标签（htmlLabels:false），回避 <foreignObject> 在大量中文标签下的渲染挂起。
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import mermaid from 'mermaid'
import { ZoomIn, ZoomOut, Scan, RotateCcw } from 'lucide-react'

let uidCounter = 0

/**
 * 全局串行渲染队列。
 * mermaid.render 并发调用会互相干扰甚至永久挂起（协作关系图节点多、耗时久），
 * 这里把所有渲染串行化：前一个完成后才开始下一个，杜绝状态损坏。
 */
let renderQueue: Promise<unknown> = Promise.resolve()
function enqueueRender(renderId: string, chart: string): Promise<unknown> {
  const task = renderQueue.then(() => mermaid.render(renderId, chart))
  // 无论成功失败都继续推进队列，避免一次失败卡死后续渲染
  renderQueue = task.catch(() => undefined)
  return task
}

interface MermaidDiagramProps {
  /** mermaid 图语法文本（graph TD / flowchart 等） */
  chart: string
  /** 容器高度 */
  height?: number | string
  className?: string
}

/** 计算十六进制颜色亮度（0~1），用于判断当前分区深浅 */
function luminance(hex: string): number {
  const m = hex.replace('#', '')
  const full =
    m.length === 3
      ? m
          .split('')
          .map((c) => c + c)
          .join('')
      : m.length === 6
        ? m
        : 'ffffff'
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255
}

export function MermaidDiagram({ chart, height = 520, className }: MermaidDiagramProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // 外层稳定容器 ref：观察它而非滚动容器，避免「放大→出现滚动条→clientWidth 变化→
  // 触发 refit 把缩放打回」的反馈环（修复协作图/组织架构无法持续放大）。
  const outerRef = useRef<HTMLDivElement>(null)
  const baseIdRef = useRef(`mermaid-${++uidCounter}`)
  const naturalRef = useRef({ w: 800, h: 400 })
  const [scale, setScale] = useState(1)
  const [error, setError] = useState<string | null>(null)
  // 主题切换计数器：亮/暗模式切换时 <html> 的 class 变化，此处自增以触发重渲染
  // （mermaid 的节点文字/连线颜色是按渲染时读到的 CSS 变量决定的，不重渲染则
  //  深色模式仍沿用浅色配色，出现「深色底 + 深色文字」不可见问题）。
  const [themeTick, setThemeTick] = useState(0)

  const fitToWidth = useCallback(() => {
    const host = hostRef.current
    const scroll = scrollRef.current
    if (!host || !scroll) return
    const vb = host.querySelector('svg')?.getAttribute('viewBox')
    const parts = (vb || '').split(/\s+/).map(Number)
    const w = parts[2] || naturalRef.current.w
    const container = scroll.clientWidth || 800
    if (w > 0) {
      // 按容器宽度完整适配（保证整图可见，不出现「缩放后落在空白角落」）。
      // 但协作关系图这类超大图（数千像素）若严格 fit 到容器宽度，会缩成微缩图、
      // 文字完全不可读。这里设一个「可读性下限」：默认以不低于该比例呈现，
      // 过宽时靠横向滚动 + 手动放大查看细节。
      const fit = Math.min(container / w, 2)
      const MIN_DEFAULT_SCALE = 0.6
      setScale(Number(Math.max(fit, MIN_DEFAULT_SCALE).toFixed(2)) || 1)
    }
  }, [])

  useEffect(() => {
    // 监听 <html> 的 class 变化（亮/暗主题切换），自增 themeTick 触发图表重渲染。
    // 亮/暗模式通过 documentElement.classList.toggle('dark') 切换（hooks/useTheme.tsx）。
    if (typeof MutationObserver === 'undefined') return
    const observer = new MutationObserver(() => setThemeTick((t) => t + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    // 读取当前分区 CSS 语义变量，适配深浅色
    const cs = getComputedStyle(host)
    const pick = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
    const bg = pick('--bg-surface', '#FFFFFF')
    const brand = pick('--brand', '#1E3A5F')
    const text = pick('--text-primary', '#1A1A1A')
    const line = pick('--border-strong', '#D9D6CC')
    const dark = luminance(text) > 0.6
    // 浅色模式下节点文字必须用深色，否则白字叠浅色底板完全不可见（修复浅色模式）。
    // 深色模式用浅色文字。连线同理：浅色模式用深灰，深色模式用分区边框色。
    const nodeText = dark ? '#E8EDF2' : '#1A1A1A'
    const edgeLine = dark ? line : '#6B6B6B'

    mermaid.initialize({
      startOnLoad: false,
      theme: 'base',
      // P2 安全加固：'loose' 会放开 HTML 标签与 click 回调（javascript: URL），
      // 图文本若含用户可控内容即为 XSS 注入面。改用 'strict' 由 mermaid 转义标签。
      // 本仓图文本由 utils/mermaidCharts.ts 生成，safeLabel() 已剥离 < > ; 等字符，
      // 且不产生 click 指令，故收紧后不影响渲染。
      securityLevel: 'strict',
      // 协作关系图节点多、边数可达数百，默认 maxEdges=500 会抛
      // "Edge limit exceeded" 导致整图渲染失败。调高以容纳完整协作网。
      maxEdges: 5000,
      maxTextSize: 200000,
      fontFamily: "Inter, system-ui, 'PingFang SC', 'Microsoft YaHei', sans-serif",
      flowchart: {
        curve: 'basis',
        // 压缩间距：协作关系图节点多、边数多，默认间距会撑出数千像素的画布，
        // 导致整图默认过小、需要大量滚动。收紧间距让图更紧凑、默认更可读。
        // 但过小会与子图/长中文标签发生节点重叠，故取一个平衡值（修复组织架构重叠）。
        padding: 8,
        nodeSpacing: 22,
        rankSpacing: 28,
        // 用 SVG 标签而非 <foreignObject>，规避中文多标签下的渲染挂起
        htmlLabels: false,
      },
      themeVariables: {
        darkMode: dark,
        background: dark ? '#0A0E13' : bg,
        primaryColor: dark ? '#16233B' : 'rgba(30, 58, 95, 0.08)',
        primaryTextColor: dark ? '#E8EDF2' : brand,
        primaryBorderColor: dark ? '#4C8DFF' : brand,
        lineColor: edgeLine,
        secondaryColor: dark ? '#101820' : '#F5F4EF',
        tertiaryColor: dark ? '#16233B' : '#EFEEE8',
        textColor: text,
        nodeTextColor: nodeText,
        clusterBkg: dark ? '#0E141B' : '#F5F4EF',
        clusterBorder: edgeLine,
        edgeLabelBackground: dark ? '#0A0E13' : bg,
        titleColor: dark ? '#E8EDF2' : brand,
        fontSize: '14px',
      },
      // mermaid 类型声明未包含 maxEdges（运行时支持），此处放宽类型以保留该配置
    } as Parameters<typeof mermaid.initialize>[0])

    let cancelled = false
    const renderId = `${baseIdRef.current}-${Date.now()}`
    setError(null)
    host.innerHTML = ''

    // 清理本组件注入到 body/head 的所有临时节点（div / svg / style），
    // mermaid 解析失败时「Syntax error in text」报错块就遗留在其中，
    // 必须在成功与失败路径上都清理，避免跨路由持续显示（#2）。
    // 注意：mermaid.render 成功返回的 SVG 其 id 恰好是 renderId（= `mermaid-N-时间戳`），
    // 直接按 id 前缀删除会把已渲染进 host 的成品 SVG 一并移除，导致图表空白。
    // 因此这里跳过 host 内部的节点，只清理 host 外 body/head 的游离临时节点。
    const cleanupInjected = () => {
      document.querySelectorAll(`[id^="${baseIdRef.current}-"]`).forEach((n) => {
        if (host.contains(n)) return
        n.remove()
      })
      document.querySelectorAll(`[id^="d${baseIdRef.current}-"]`).forEach((n) => {
        if (host.contains(n)) return
        n.remove()
      })
    }

    const timeout = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error('图表渲染超时')), 8000)
    )

    Promise.race([enqueueRender(renderId, chart), timeout])
      .then((res) => {
        if (cancelled) return
        const svg = (res as { svg?: string } | undefined)?.svg
        if (!svg) throw new Error('图表渲染结果为空')
        host.innerHTML = svg
        const svgEl = host.querySelector('svg')
        if (svgEl) {
          const vb = svgEl.getAttribute('viewBox')
          const parts = (vb || '').split(/\s+/).map(Number)
          if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
            naturalRef.current = { w: parts[2], h: parts[3] }
          }
          svgEl.setAttribute('width', '100%')
          svgEl.setAttribute('height', '100%')
          svgEl.style.display = 'block'
        }
        fitToWidth()
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : '图表渲染失败')
        }
      })
      .finally(() => {
        cleanupInjected()
      })

    return () => {
      cancelled = true
      cleanupInjected()
    }
  }, [chart, fitToWidth, themeTick])

  useEffect(() => {
    // 外层容器尺寸变化（如侧栏折叠 / 窗口缩放）时重新适配宽度。
    // 观察 outerRef（稳定外层）而非 scrollRef（滚动容器）：当用户放大时，
    // 内部 wrapper 变宽只是让 scrollRef 出现滚动条、其 clientWidth 抖动，
    // 若观察 scrollRef 会把用户缩放反复打回（反馈环）。观察外层只有真实
    // 的页面容器变化（侧栏/窗口）才会触发，从而既适配又保留用户缩放。
    if (typeof ResizeObserver === 'undefined') return
    const outer = outerRef.current
    if (!outer) return
    let lastW = outer.clientWidth
    const ro = new ResizeObserver(() => {
      const w = outer.clientWidth
      if (Math.abs(w - lastW) > 1) {
        lastW = w
        fitToWidth()
      }
    })
    ro.observe(outer)
    return () => ro.disconnect()
  }, [fitToWidth])

  const zoomBy = (factor: number) =>
    setScale((s) => Math.min(Math.max(Number((s * factor).toFixed(2)), 0.2), 6))

  return (
    <div ref={outerRef} className={`relative ${className ?? ''}`} style={{ height }}>
      <div
        ref={scrollRef}
        className="overflow-auto rounded-lg border border-border-default bg-surface"
        style={{ height: '100%' }}
      >
        <div
          className="mermaid-inner"
          style={{
            width: naturalRef.current.w * scale,
            height: naturalRef.current.h * scale,
          }}
        >
          <div ref={hostRef} />
        </div>
      </div>

      {/* 缩放控制条 */}
      <div className="absolute top-3 right-3 flex items-center gap-0.5 rounded-md border border-border-default bg-surface/90 backdrop-blur px-1 py-0.5 shadow-soft">
        <button
          type="button"
          onClick={() => zoomBy(1.2)}
          title="放大"
          className="p-1.5 rounded text-text-secondary hover:bg-elevated hover:text-brand-500 transition-colors"
        >
          <ZoomIn className="w-4 h-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => zoomBy(0.8)}
          title="缩小"
          className="p-1.5 rounded text-text-secondary hover:bg-elevated hover:text-brand-500 transition-colors"
        >
          <ZoomOut className="w-4 h-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={fitToWidth}
          title="适应宽度"
          className="p-1.5 rounded text-text-secondary hover:bg-elevated hover:text-brand-500 transition-colors"
        >
          <Scan className="w-4 h-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setScale(1)}
          title="原始大小"
          className="p-1.5 rounded text-text-secondary hover:bg-elevated hover:text-brand-500 transition-colors"
        >
          <RotateCcw className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>

      {error && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="max-w-sm text-center text-sm text-error bg-surface/90 rounded-lg border border-error/30 px-4 py-3">
            图表渲染失败，请检查数据或稍后重试
          </div>
        </div>
      )}
    </div>
  )
}

export default MermaidDiagram