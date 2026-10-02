import { useEffect, useMemo, useRef } from 'react'

/**
 * VitalPulse — 企业脉搏（UI v4 核心视觉母题）
 *
 * 全站贯穿的差异化记忆点：一根**由真实后端数据驱动**的心电波形。
 *   - 振幅 ← 活跃 Agent 数（在岗越多，心跳越有力）
 *   - 频率 ← 协作事件流速率（业务越忙，心跳越快）
 *   - 颜色 ← 系统健康度（青=健康 / 琥珀=有待审批 / 红=有失败事件）
 *   - 编译进行中时切换为扫描线形态（scanning）
 *
 * 这不是装饰动画：所有参数都来自 /cognition/vitals 的真实聚合指标。
 * 目的是让「这家 AI 公司此刻正活着」在 3 秒内可见。
 *
 * 实现用 Canvas + requestAnimationFrame 而非 SVG —— 高频重绘下 SVG 会掉帧。
 */

export type PulseTone = 'alive' | 'alert' | 'fault' | 'idle'

interface VitalPulseProps {
  /** 活跃 Agent 数 → 波形振幅 */
  activeCount?: number
  /** 每小时事件数 → 心跳频率 */
  eventsPerHour?: number
  /** 健康语义 → 波形颜色 */
  tone?: PulseTone
  /** 编译中：波形切换为扫描线形态 */
  scanning?: boolean
  /** 画布高度（px） */
  height?: number
  className?: string
}

const TONE_COLOR: Record<PulseTone, string> = {
  alive: '#4DD8C0',
  alert: '#F5A524',
  fault: '#FF5D5D',
  idle: '#64748B',
}

/** 单个心跳周期的波形（归一化到 0-1 的相位 → -1~1 的幅值） */
function heartbeatAt(phase: number): number {
  // P 波（心房）→ QRS 波群（心室，主峰）→ T 波（复极）
  if (phase < 0.12) return Math.sin(phase / 0.12 * Math.PI) * 0.16
  if (phase < 0.2) return 0
  if (phase < 0.24) return -((phase - 0.2) / 0.04) * 0.22 // Q 下冲
  if (phase < 0.29) return -0.22 + ((phase - 0.24) / 0.05) * 1.22 // R 主峰上冲
  if (phase < 0.35) return 1.0 - ((phase - 0.29) / 0.06) * 1.42 // S 回落
  if (phase < 0.42) return -0.42 + ((phase - 0.35) / 0.07) * 0.42 // 回归基线
  if (phase < 0.62) return Math.sin((phase - 0.42) / 0.2 * Math.PI) * 0.28 // T 波
  return 0
}

export function VitalPulse({
  activeCount = 0,
  eventsPerHour = 0,
  tone = 'alive',
  scanning = false,
  height = 56,
  className = '',
}: VitalPulseProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>()
  // 用 ref 持有最新参数，避免每次 props 变化都重启动画循环（否则波形会跳变）
  const paramsRef = useRef({ activeCount, eventsPerHour, tone, scanning })
  paramsRef.current = { activeCount, eventsPerHour, tone, scanning }

  const reducedMotion = useMemo(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let width = 0
    let h = 0
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      width = Math.max(1, rect.width)
      h = Math.max(1, rect.height)
      canvas.width = width * dpr
      canvas.height = h * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    // 静态降级：不播放动画，只绘制一条基线 + 一个静止心跳
    if (reducedMotion) {
      const { tone: t } = paramsRef.current
      ctx.clearRect(0, 0, width, h)
      ctx.strokeStyle = TONE_COLOR[t]
      ctx.lineWidth = 1.5
      ctx.globalAlpha = 0.85
      ctx.beginPath()
      for (let x = 0; x <= width; x++) {
        const phase = ((x / width) * 2) % 1
        const y = h / 2 - heartbeatAt(phase) * (h * 0.34)
        if (x === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
      return () => ro.disconnect()
    }

    const t0 = performance.now()

    const draw = (now: number) => {
      const { activeCount: ac, eventsPerHour: eph, tone: tn, scanning: sc } = paramsRef.current
      const elapsed = (now - t0) / 1000
      const color = TONE_COLOR[tn]

      ctx.clearRect(0, 0, width, h)

      // 基线
      ctx.strokeStyle = 'rgba(255,255,255,0.07)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, h / 2)
      ctx.lineTo(width, h / 2)
      ctx.stroke()

      if (sc) {
        // 编译中：扫描线形态（一束光横向扫过 + 残影网格）
        const sweep = (elapsed * 0.55) % 1
        const cx = sweep * width
        const grad = ctx.createLinearGradient(cx - 90, 0, cx + 90, 0)
        grad.addColorStop(0, 'rgba(76,141,255,0)')
        grad.addColorStop(0.5, '#4C8DFF')
        grad.addColorStop(1, 'rgba(76,141,255,0)')
        ctx.strokeStyle = grad
        ctx.lineWidth = 2
        ctx.beginPath()
        for (let x = 0; x <= width; x++) {
          const d = Math.abs(x - cx)
          const amp = Math.exp(-(d * d) / 2600) * (h * 0.32)
          const y = h / 2 - Math.sin(x * 0.09 + elapsed * 7) * amp
          if (x === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.stroke()
      } else {
        // 常态：灵动曲线（灵动的「跳跃」而非呆板平线）
        //
        // 视觉目标：像一条有生命力的脉搏——
        //   1. 多层正弦叠加出前后「流动」的有机浪（曲线本身流畅、不跳变）
        //   2. 一道明显的波峰沿曲线向前「奔跑」，且在行进中上下起伏（这才是「跳跃」）
        // 振幅/频率驱动真实数据：活跃 Agent 越多越有力，事件流越快越灵动。
        const vigor = Math.min(1, ac / 10)
        const amp = h * (0.24 + vigor * 0.34)
        const speed = 0.5 + Math.min(0.8, eph / 80)
        const t = elapsed * speed

        // 曲线上某点 y：有机流动主浪 + 一道向前奔跑且上下跳动的波峰
        const lineY = (x: number, tv: number) => {
          const flow =
            Math.sin(x * 0.02 + tv * 1.1) * 0.6 +
            Math.sin(x * 0.045 - tv * 1.6 + 2.3) * 0.28 +
            Math.sin(x * 0.008 + tv * 0.45 + 4.1) * 0.12
          // 波峰位置沿曲线前进：tv 增大 → bumpX 右移
          const bumpX = ((tv * 0.22) % 1) * width
          const dd = Math.min(Math.abs(x - bumpX), width - Math.abs(x - bumpX))
          const envelope = Math.exp(-(dd * dd) / (width * width * 0.004))
          // 上下跳动：峰在抵达前积蓄、抵达时冲高、越过回落，形成鲜活的「跃动」
          const bounce = 0.5 + 0.5 * Math.sin(tv * 5.5)
          const bump = envelope * bounce * 1.1
          return h / 2 - (flow + bump) * amp
        }

        // 呼吸底色：低透明度缓慢起伏的软波，让整体有「活」的节奏
        ctx.strokeStyle = color
        ctx.globalAlpha = 0.13
        ctx.lineWidth = 1.2
        ctx.beginPath()
        for (let x = 0; x <= width; x++) {
          const y = h / 2 - Math.sin(x * 0.012 + elapsed * 1.1) * amp * 0.42
          if (x === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.stroke()
        ctx.globalAlpha = 1

        // 主曲线：辉光描边，双通道叠加制造「跃动」光晕
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'
        ctx.shadowColor = color
        for (let pass = 0; pass < 2; pass++) {
          ctx.strokeStyle = color
          ctx.globalAlpha = pass === 0 ? 0.3 : 1
          ctx.lineWidth = pass === 0 ? 4.5 : 2
          ctx.shadowBlur = pass === 0 ? 14 : 8
          ctx.beginPath()
          for (let x = 0; x <= width; x++) {
            const y = lineY(x, t)
            if (x === 0) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
          }
          ctx.stroke()
        }
        ctx.shadowBlur = 0
        ctx.globalAlpha = 1

        // 游标光点：跟随奔跑的波峰，强化「正在跳跃」的感知
        const headX = ((t * 0.22) % 1) * width
        const headY = lineY(headX, t)
        ctx.fillStyle = color
        ctx.globalAlpha = 0.22
        ctx.beginPath()
        ctx.arc(headX, headY, 6, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalAlpha = 0.9
        ctx.beginPath()
        ctx.arc(headX, headY, 2.6, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalAlpha = 1
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      ro.disconnect()
    }
  }, [reducedMotion])

  return (
    <canvas
      ref={canvasRef}
      className={`w-full block ${className}`}
      style={{ height }}
      role="img"
      aria-label={
        scanning
          ? '企业编译进行中'
          : `企业脉搏：${activeCount} 个 AI 员工在岗，每小时 ${eventsPerHour} 个协作事件`
      }
    />
  )
}
