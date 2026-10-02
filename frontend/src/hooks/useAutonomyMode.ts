/**
 * useAutonomyMode — 渐进式自主模式（PRD §2.2 / UI 方案 §2.1）
 *
 * 集中管理「老板/对话/文件/自有」四种模式，持久化到 localStorage，
 * 并通过自定义事件在同标签页内实时同步（storage 事件仅跨标签页触发）。
 *
 * 用法：
 *   const mode = useAutonomyMode()          // 读取当前模式
 *   setAutonomyMode('boss')                 // 切换模式（全局生效）
 */
import { useState, useEffect } from 'react'

export type AutonomyMode = 'boss' | 'conversation' | 'file' | 'own'

export const AUTONOMY_STORAGE_KEY = 'autoteams_autonomy_mode'

/** 同标签页内同步模式变更的自定义事件名 */
export const AUTONOMY_CHANGE_EVENT = 'autoteams:autonomy-change'

export const AUTONOMY_MODES: ReadonlyArray<{ id: AutonomyMode; label: string; desc: string }> = [
  { id: 'boss', label: '老板模式', desc: '一键部署 AI 数字员工，最小摩擦。适合企业老板 / 决策者。' },
  { id: 'conversation', label: '对话模式', desc: '通过对话调整或增加 AI 员工技能、更改业务流程。适合业务人员。' },
  { id: 'file', label: '文件模式', desc: '直接修改文件来调整 AI 员工配置。适合业务人员。' },
  { id: 'own', label: '自有模式', desc: '用代码等方式深度调整 AI 员工和运行模型。适合高级 / 懂行用户。' },
]

function readMode(): AutonomyMode {
  try {
    const raw = localStorage.getItem(AUTONOMY_STORAGE_KEY)
    if (raw && AUTONOMY_MODES.some((m) => m.id === raw)) return raw as AutonomyMode
  } catch { /* ignore */ }
  return 'boss'
}

/** 读取当前自主模式（默认老板模式）。 */
export function useAutonomyMode(): AutonomyMode {
  const [mode, setMode] = useState<AutonomyMode>(readMode)

  useEffect(() => {
    const sync = () => setMode(readMode())
    window.addEventListener(AUTONOMY_CHANGE_EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(AUTONOMY_CHANGE_EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])

  return mode
}

/** 切换自主模式并广播变更（同标签页 + 跨标签页）。 */
export function setAutonomyMode(mode: AutonomyMode): void {
  try {
    localStorage.setItem(AUTONOMY_STORAGE_KEY, mode)
  } catch { /* ignore */ }
  window.dispatchEvent(new CustomEvent(AUTONOMY_CHANGE_EVENT, { detail: mode }))
}

/**
 * 判断某导航路径在当前模式下是否可见。
 * 老板模式隐藏部分高级技术入口以降低首用摩擦；企业构建/编译/运行时
 * 是核心主流程入口，始终可见。
 */
export function isNavVisibleForMode(mode: AutonomyMode, path: string): boolean {
  if (mode !== 'boss') return true
  const advancedPaths = ['/shadow-mode-demo', '/audit-logs']
  return !advancedPaths.includes(path)
}