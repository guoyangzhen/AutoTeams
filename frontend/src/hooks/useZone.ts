/**
 * 分区视觉语言（UI v4 §3.1）
 *
 * 设计理念：不是「深色模式开关」，而是按**使用场景**做功能性分工。
 *
 *   指挥区（command）：驾驶舱/编译/运行时/影子/进化/组织架构
 *     —— 观察态。深空石墨底 + 信号色发光，数据自身成为光源，沉浸感强。
 *
 *   工作区（workspace）：对话/知识/技能/设置/访谈
 *     —— 操作态。保留暖白纸感，长时间阅读与输入不伤眼。
 *
 * 融合机制（避免割裂的三个关键设计）：
 *   1. 同名语义变量：两区共用 --bg-surface / --text-primary 等同一组变量名，
 *      组件代码完全不感知所在分区，杜绝「两套组件」导致的风格漂移。
 *   2. 共享骨架：字体、圆角、间距、动效时长曲线在两区完全一致，
 *      只有色彩明度反转 —— 结构统一，观感自然连续。
 *   3. 过渡仪式：切换时播放 420ms「座舱上电」动画，把突变转为叙事，
 *      用户感知到的是「进入了另一个功能区」而非「页面变色了」。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useTheme } from '@/hooks/useTheme'

export type ZoneKind = 'command' | 'workspace'

/**
 * 指挥区路由前缀（观察类页面）。
 * 判定基于 pathname 前缀，hub 页面的 ?tab= 子页面自动继承所属 hub 的分区。
 */
const COMMAND_ROUTES = [
  '/company',        // 驾驶舱：今日 AI 公司
  '/build',          // 构建工坊：五级编译 + 运行时
  '/evolution',      // 进化中心
  '/shadow-mode-demo', // 影子模式
  '/audit-logs',     // 审计日志
  '/dashboard',      // 老板看板
  '/loop',           // 闭环仪表盘
  '/canvas',         // 构建画布
  '/compile',        // 旧路由兜底
  '/runtime',
  '/organization',
] as const

/** 解析路径所属分区 */
export function resolveZone(pathname: string): ZoneKind {
  const hit = COMMAND_ROUTES.some(
    (r) => pathname === r || pathname.startsWith(r + '/') || pathname.startsWith(r + '?'),
  )
  return hit ? 'command' : 'workspace'
}

/**
 * 当前路由所属分区。
 * 返回 zone 与应挂载到根容器的 className。
 */
export function useZone(): { zone: ZoneKind; zoneClass: string } {
  const location = useLocation()
  const { theme } = useTheme()
  const zone = useMemo(() => resolveZone(location.pathname), [location.pathname])

  // 同步到 <html>，让 body 背景、原生滚动条、表单控件配色跟随全局主题，
  // 避免页面边缘露出与主题不符的底色（这是深浅切换最容易穿帮的地方）。
  // 全站真正切换：暗色模式（theme === 'dark'）下全站为深色，亮色下全站为浅色，
  // 指挥区不再单独强制深色。
  useEffect(() => {
    const root = document.documentElement
    const isDark = theme === 'dark'
    root.style.colorScheme = isDark ? 'dark' : 'light'
    root.style.backgroundColor = isDark ? '#0A0E13' : '#FAFAF7'
  }, [zone, theme])

  return {
    zone,
    zoneClass: zone === 'command' ? 'zone-command' : '',
  }
}

/**
 * 首次进入指挥区时播放「座舱上电」过场（420ms）。
 * 仅在 workspace → command 的切换边界触发，同区内导航不重复播放，
 * 避免动画疲劳。
 */
export function useZoneBoot(zone: ZoneKind): boolean {
  const [booting, setBooting] = useState(false)
  // 上一次分区用 ref 记录：读写 sessionStorage 属于副作用，
  // 放在 useMemo 里会在 StrictMode 双调用下产生错误结论（第二次读到自己刚写的值）。
  const prevZoneRef = useRef<ZoneKind | null>(null)

  useEffect(() => {
    if (prevZoneRef.current === null) {
      try {
        prevZoneRef.current = (sessionStorage.getItem('autoteams_prev_zone') as ZoneKind) || null
      } catch {
        prevZoneRef.current = null
      }
    }
    const crossed = zone === 'command' && prevZoneRef.current !== 'command'
    prevZoneRef.current = zone
    try {
      sessionStorage.setItem('autoteams_prev_zone', zone)
    } catch {
      /* 隐私模式下 sessionStorage 不可写，退化为不播放过场 */
    }
    if (!crossed) {
      setBooting(false)
      return
    }
    setBooting(true)
    const timer = setTimeout(() => setBooting(false), 460)
    return () => clearTimeout(timer)
  }, [zone])

  return booting
}
