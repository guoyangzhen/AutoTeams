import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from 'react'

/**
 * 主题上下文（#21：重建亮/暗双主题）。
 *
 * 设计说明：
 * - 保留 ThemeProvider/useTheme API（App.tsx 的 ThemedToaster、Layout 切换入口等复用）。
 * - theme 为 'light' | 'dark'，默认浅色（沿用 v4 分区视觉：浅色下指挥区仍为深空仪表）。
 * - 通过给 <html> 挂 .light / .dark 类驱动全局暗色层（见 index.css 的 html.dark 变量块），
 *   全站语义变量适配，暗色下文字由语义变量保证可见。
 * - 偏好持久化到 localStorage，避免刷新丢失。
 */
type Theme = 'light' | 'dark'

interface ThemeContextValue {
  theme: Theme
  toggleTheme: () => void
  setTheme: (t: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

const STORAGE_KEY = 'autoteams_theme'

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark') return 'dark'
  } catch {
    /* 隐私模式等场景下 localStorage 不可读，退化为浅色 */
  }
  return 'light'
}

function applyTheme(theme: Theme) {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.classList.toggle('light', theme === 'light')
  // 说明：colorScheme / backgroundColor 交由 CSS（html.dark）与 useZone 处理，
  // 避免此处内联样式覆盖 useZone 在指挥区/工作区间的正确判断。
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    applyTheme(theme)
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* 忽略写入失败 */
    }
  }, [theme])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
  }, [])

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}