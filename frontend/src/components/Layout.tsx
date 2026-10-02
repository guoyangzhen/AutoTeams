import { ReactNode, useState, useEffect, useCallback, useMemo, useRef, createContext, useContext } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  Menu, X, LogOut, Settings, Search, Bell, History, Database,
  Shield, Sun, Moon,
  Building2, Users, TrendingUp, Layers, CheckCircle2, ChevronRight,
  PanelLeftClose, PanelLeftOpen, Workflow, Plug, BarChart3,
} from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { useAuth } from '@/hooks/useAuth'
import { useTheme } from '@/hooks/useTheme'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { useAutonomyMode, isNavVisibleForMode } from '@/hooks/useAutonomyMode'
import { useZone, useZoneBoot } from '@/hooks/useZone'
import { CommandPalette, type CommandItem } from '@/components/ui/CommandPalette'
import { Logo } from '@/components/Logo'
import { useNavigationData } from '@/hooks/useNavigationData'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { usePopoverPanel, type PopoverCloseReason } from '@/hooks/usePopoverPanel'
import { positionToLabel } from '@/utils/fieldMappings'

// ============================================================
// 嵌入式上下文：hub 页面合并子页面时，子页面的 <Layout> 退化为透传容器，
// 避免出现双重侧边栏。子页面代码无需任何改动。
// ============================================================

const EmbeddedContext = createContext(false)

/** 标记子树为「嵌入式」——内部的 <Layout> 将只渲染 children，不再输出侧边栏/顶栏。 */
export function EmbeddedProvider({ children }: { children: ReactNode }) {
  return <EmbeddedContext.Provider value={true}>{children}</EmbeddedContext.Provider>
}

interface LayoutProps {
  children: ReactNode
  /**
   * 流式布局：取消 max-w-7xl 容器与内边距，让子页面（如画布）撑满视口剩余高度。
   * 顶部品牌栏仍保留，主内容区变为 flex-1 弹性容器。
   */
  fluid?: boolean
}

// ============================================================
// 导航项分组（单一模式：所有功能在左侧边栏按业务域分组展示）
// ============================================================

interface NavItem {
  path: string
  label: string
  icon: typeof Building2
  adminOnly?: boolean
  /** 搜索同义词（拼音首字母 / 英文 / 别名） */
  keywords?: string[]
}

interface NavGroup {
  id: string
  label: string
  items: NavItem[]
}

/**
 * 左侧栏导航：收敛为 9 大核心工作空间（重构方案 §4.3.1）中需要侧边栏承载的入口。
 * 旧的「规程与协同 / 连接与执行 / 进化与推演」三块高阶工作台已并入工作流、
 * Agent 接入、数据看板，路由仍保留但不再出现在导航中，避免入口过载。
 */
const navGroups: NavGroup[] = [
  {
    id: 'workspaces',
    label: '核心工作台',
    items: [
      {
        path: '/dashboard',
        label: '总览驾驶舱',
        icon: TrendingUp,
        keywords: ['首页', '总览', '指挥', '驾驶舱', '履约', 'boss', 'dashboard', 'zh'],
      },
      {
        path: '/workforce',
        label: '数字员工',
        icon: Users,
        keywords: ['员工', '数字员工', '花名册', '工号', '岗位', '档案', 'workforce', 'profile', 'roster', 'yg'],
      },
      {
        path: '/workflows',
        label: '工作流',
        icon: Workflow,
        keywords: ['工作流', 'SOP', '规程', '流程', '自动化', '触发', 'workflows', 'sop', 'gc', 'xt'],
      },
      {
        path: '/agents',
        label: 'Agent 接入',
        icon: Plug,
        keywords: ['Agent', '接入', 'Codex', 'Claude', 'MCP', '本地', '执行器', '桥接', 'agents', 'mcp', 'codex', 'jr'],
      },
      {
        path: '/analytics',
        label: '数据看板',
        icon: BarChart3,
        keywords: ['看板', '数据', 'ROI', '成本', '使用量', '统计', 'analytics', 'roi', 'shuju', 'kb'],
      },
    ],
  },
  {
    id: 'governance',
    label: '系统治理',
    items: [
      {
        path: '/audit-logs',
        label: '审计日志',
        icon: Shield,
        adminOnly: true,
        keywords: ['审计', '日志', 'audit', 'log', 'sj'],
      },
      {
        path: '/settings',
        label: '组织设置',
        icon: Settings,
        keywords: ['设置', '配置', '组织', '密钥', '模型', 'settings', 'sz'],
      },
    ],
  },
]

export default function Layout({ children, fluid = false }: LayoutProps) {
  // 嵌入式模式：hub 页面已提供侧边栏/顶栏，此处退化为透传，避免双重 chrome
  const embedded = useContext(EmbeddedContext)
  if (embedded) return <>{children}</>
  return <LayoutChrome fluid={fluid}>{children}</LayoutChrome>
}

function LayoutChrome({ children, fluid = false }: LayoutProps) {
  const reducedMotion = useReducedMotion()
  const { user, logout } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const enterpriseId = useEnterpriseId()
  const location = useLocation()
  const autonomyMode = useAutonomyMode()
  // v4 分区视觉语言：指挥区（观察态，深空仪表）/ 工作区（操作态，暖白纸感）
  const { zoneClass } = useZone()
  const booting = useZoneBoot(zoneClass ? 'command' : 'workspace')
  const [sidebarOpen, setSidebarOpen] = useState(false) // 移动端抽屉开关
  // #1 桌面端「折叠为仅图标」状态（持久化，避免刷新后重置）
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('autoteams_sidebar_collapsed') === '1'
    } catch {
      return false
    }
  })
  const toggleSidebarCollapsed = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem('autoteams_sidebar_collapsed', next ? '1' : '0')
      } catch {
        /* 忽略 */
      }
      return next
    })
  }, [])
  // 顶栏三个下拉浮层互斥：同一时刻只开一个（互斥由 usePopoverPanel 的模块登记负责）。
  type PopoverId = 'notifications' | 'history' | 'user'
  const [openPopover, setOpenPopover] = useState<PopoverId | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const notifOpen = openPopover === 'notifications'
  const historyOpen = openPopover === 'history'
  const userMenuOpen = openPopover === 'user'
  const navigation = useNavigationData(enterpriseId, user?.id ?? null)
  const { agents: searchAgents, files: searchFiles, approvalIds, approvalsLoading, approvalsError } = navigation
  const pendingApprovalCount = approvalIds.length
  const [readNotifications, setReadNotifications] = useState<{ key: string; ids: string[] } | null>(null)
  const [history, setHistory] = useState<{ path: string; label: string; time: number }[]>([])
  const mobileNavigationRef = useRef<HTMLElement>(null)
  const closeMobileNavigation = useCallback(() => setSidebarOpen(false), [])
  useFocusTrap({ active: sidebarOpen, containerRef: mobileNavigationRef, onClose: closeMobileNavigation })

  // 浮层关闭：还焦与互斥由 hook 负责，这里只收起状态。
  const closePopover = useCallback((_reason: PopoverCloseReason) => setOpenPopover(null), [])
  const notifTriggerRef = useRef<HTMLButtonElement>(null)
  const notifPanelRef = useRef<HTMLDivElement>(null)
  const historyTriggerRef = useRef<HTMLButtonElement>(null)
  const historyPanelRef = useRef<HTMLDivElement>(null)
  const userTriggerRef = useRef<HTMLButtonElement>(null)
  const userPanelRef = useRef<HTMLDivElement>(null)
  usePopoverPanel({ open: notifOpen, panelRef: notifPanelRef, triggerRef: notifTriggerRef, onClose: closePopover })
  usePopoverPanel({ open: historyOpen, panelRef: historyPanelRef, triggerRef: historyTriggerRef, onClose: closePopover })
  usePopoverPanel({ open: userMenuOpen, panelRef: userPanelRef, triggerRef: userTriggerRef, onClose: closePopover })

  useEffect(() => {
    setPaletteOpen(false)
    setOpenPopover(null)
    setSidebarOpen(false)
  }, [navigation.key])

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 768px)')
    const closeOnDesktop = () => { if (desktop.matches) setSidebarOpen(false) }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])

  // 全局快捷键：⌘K / Ctrl+K 打开命令面板
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.isComposing || e.keyCode === 229) return
      // 已打开的模态拥有当前交互；全局搜索不能再打开一个位于 inert 背景里的浮层。
      if (!paletteOpen && document.querySelector('[role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"]')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpenPopover(null)
        setPaletteOpen((o) => !o)
        return
      }
      if (e.key === '/' && !paletteOpen) {
        const target = e.target as HTMLElement
        const tag = target?.tagName?.toLowerCase()
        if (tag !== 'input' && tag !== 'textarea' && !target?.isContentEditable) {
          setOpenPopover(null)
          setPaletteOpen(true)
        }
      }
    }
    window.addEventListener('keydown', handler, true)
    return () => window.removeEventListener('keydown', handler, true)
  }, [paletteOpen])

  const handleLogout = async () => {
    await logout()
  }

  const isActive = (path: string) => {
    const p = location.pathname
    if (path === '/dashboard') return p === '/dashboard' || p === '/' || p === '/company'
    if (path === '/workforce') return p === '/workforce' || p.startsWith('/workforce') || p === '/workforce-gallery' || p === '/employees'
    return p === path || p.startsWith(path + '/')
  }
  // 路由变化时关闭移动端侧边栏 + 记录操作历史
  useEffect(() => {
    setSidebarOpen(false)
    // 记录页面访问历史（最多5条，去重）
    const path = location.pathname
    if (path === '/' || path === '/login') return
    const navItem = navGroups.flatMap((g) => g.items).find((i) => path === i.path || path.startsWith(i.path + '/'))
    if (!navItem) return
    setHistory((prev) => {
      const filtered = prev.filter((h) => h.path !== path)
      const next = [{ path, label: navItem.label, time: Date.now() }, ...filtered].slice(0, 5)
      try { localStorage.setItem('autoteams_nav_history', JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [location.pathname])

  // 初始化：从 localStorage 加载历史
  useEffect(() => {
    try {
      const raw = localStorage.getItem('autoteams_nav_history')
      if (raw) setHistory(JSON.parse(raw))
    } catch { /* ignore */ }
  }, [])

  // 命令面板条目：聚合所有可见导航 + 员工 + 知识库文件 + 常用动作
  const commandItems = useMemo<CommandItem[]>(() => {
    const navToCommands = (items: NavItem[], group: string): CommandItem[] =>
      items
        .filter((i) => !i.adminOnly || user?.role === 'admin')
        .filter((i) => isNavVisibleForMode(autonomyMode, i.path))
        .map((i) => ({
          id: `nav-${i.path}`,
          label: i.label,
          group,
          icon: i.icon,
          to: i.path,
          keywords: [...(i.keywords || []), i.path],
        }))
    const groups: CommandItem[] = []
    navGroups.forEach((g) => {
      groups.push(...navToCommands(g.items, g.label))
    })

    // 员工搜索项：按姓名/岗位搜索，跳转到 AI 员工页
    const employeeItems: CommandItem[] = searchAgents.slice(0, 30).map((a) => {
      const posLabel = positionToLabel(a.position)
      return {
        id: `emp-${a.agent_id}`,
        label: a.agent_name,
        group: 'AI 员工',
        icon: Users,
        to: '/workforce',
        keywords: [posLabel, a.position, '员工', '数字员工'],
      }
    })

    // 知识库文件搜索项：按文件名搜索，跳转到知识库页
    const fileItems: CommandItem[] = searchFiles.slice(0, 30).map((f) => ({
      id: `file-${f.id}`,
      label: f.original_name,
      group: '知识库文件',
      icon: Database,
      to: '/knowledge',
      keywords: [f.file_type, '知识', '文件', '文档'],
    }))

    return [
      ...groups,
      ...employeeItems,
      ...fileItems,
    ]
  }, [user?.role, searchAgents, searchFiles, autonomyMode])

  // 打开命令面板前先收起顶栏浮层，避免两个浮层与面板争夺焦点。
  const openPalette = useCallback(() => {
    setOpenPopover(null)
    setPaletteOpen(true)
  }, [])

  // P1.3: 打开通知面板即标记为已读，红点消失
  const handleNotifToggle = useCallback(() => {
    const next = !notifOpen
    setOpenPopover(next ? 'notifications' : null)
    if (next && !approvalsLoading && !approvalsError) setReadNotifications({ key: navigation.key, ids: [...approvalIds] })
  }, [notifOpen, navigation.key, approvalIds, approvalsLoading, approvalsError])

  const handleHistoryToggle = useCallback(() => setOpenPopover(historyOpen ? null : 'history'), [historyOpen])
  const handleUserToggle = useCallback(() => setOpenPopover(userMenuOpen ? null : 'user'), [userMenuOpen])

  const displayCount = approvalIds.filter((id) => readNotifications?.key !== navigation.key || !readNotifications.ids.includes(id)).length
  const activeContext = navGroups
    .flatMap((group) => group.items.map((item) => ({ group: group.label, item })))
    .find(({ item }) => isActive(item.path))

  return (

    <div
            className={`app-shell ${zoneClass} ${booting ? 'zone-booting' : ''} theme-transition bg-[var(--bg-canvas)] ${fluid ? 'h-screen flex overflow-hidden' : 'min-h-screen flex'}`}

        >
      <a
        href="#main-content"
        className="sr-only fixed left-4 top-4 z-[60] rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-white shadow-lift focus:not-sr-only focus:outline-none focus:ring-2 focus:ring-white"
      >
        跳到主要内容
      </a>
      {/* ============ 桌面端固定左侧栏 ============

          设计决策（UI v4 §3.1 融合机制）：导航链恒定为深色仪表底，
          不随内容区分区变化。它是用户的空间锚点 —— 内容区在深/浅之间切换时，
          导航保持不动，切换被感知为「进入另一个功能区」而非「页面变色了」。 */}
      <aside
                className={`app-sidebar hidden md:flex flex-col nav-rail border-r fixed left-0 top-0 bottom-0 z-40 transition-[width] duration-200 ${
          sidebarCollapsed ? 'w-16' : 'w-60'
        }`}

      >
        <SidebarContent
          user={user}
          isActive={isActive}
          onLogout={handleLogout}
          autonomyMode={autonomyMode}
          collapsed={sidebarCollapsed}
        />
      </aside>

      {/* ============ 移动端侧边栏抽屉 ============ */}
      <AnimatePresence>
        {sidebarOpen && (
          <>
            <div
              className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm md:hidden"
              onClick={() => setSidebarOpen(false)}
            />
            <motion.aside
              ref={mobileNavigationRef}
              id="mobile-navigation"
              role="dialog"
              aria-modal="true"
              aria-label="主导航"
              initial={reducedMotion ? false : { x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ duration: reducedMotion ? 0 : 0.2, ease: 'easeOut' }}
                            className="app-sidebar fixed top-0 left-0 bottom-0 z-50 w-72 nav-rail border-r md:hidden overflow-y-auto flex flex-col"

            >
              <button type="button" onClick={closeMobileNavigation} aria-label="关闭菜单"
                className="ui-control absolute right-3 top-3 z-10 inline-flex w-10 items-center justify-center text-text-secondary hover:bg-border-subtle">
                <X className="h-5 w-5" aria-hidden="true" />
              </button>
              <SidebarContent
                user={user}
                isActive={isActive}
                onLogout={handleLogout}
                onNavigate={() => setSidebarOpen(false)}
                autonomyMode={autonomyMode}
              />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* ============ 主区域：顶部栏 + 内容 ============ */}
            <div className={`app-main flex-1 flex flex-col min-w-0 ${sidebarCollapsed ? 'md:pl-16' : 'md:pl-60'} ${fluid ? 'h-screen' : 'min-h-screen'}`}>

        {/* 顶部栏：跨分区自适应（走语义变量，深区自动转为仪表底） */}
                <header className="app-header sticky top-0 z-30 h-16 border-b flex items-center px-3 sm:px-6 gap-1 sm:gap-3 flex-shrink-0 text-[var(--text-primary)]">

          {/* 移动端菜单按钮 */}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            aria-label={sidebarOpen ? '关闭菜单' : '打开菜单'}
            aria-expanded={sidebarOpen}
            aria-controls="mobile-navigation"
            className="ui-control md:hidden inline-flex shrink-0 w-10 items-center justify-center rounded-md hover:bg-border-subtle text-text-secondary"
          >
            {sidebarOpen ? <X className="w-5 h-5" aria-hidden="true" /> : <Menu className="w-5 h-5" aria-hidden="true" />}
          </button>

          {/* 移动端 Logo */}
          <Link to="/" className="md:hidden flex items-center gap-2 flex-shrink-0" aria-label="返回工作空间首页">
            <Logo className="w-7 h-7 flex-shrink-0" variant="brand" />
          </Link>

          <div className="hidden min-w-0 md:block">
            <p className="ui-context-kicker">工作空间</p>
            <p className="mt-0.5 text-sm font-semibold tracking-[-0.015em] text-text-primary truncate">
              {activeContext ? `${activeContext.group} · ${activeContext.item.label}` : 'AutoTeams'}
            </p>
          </div>

          <div className="flex-1" />

          {/* 右侧操作区 */}
          <div className="flex items-center gap-0 sm:gap-2 flex-shrink-0">
            {/* 搜索 (桌面端，⌘K 命令面板入口) */}
            <button
              onClick={openPalette}
                            className="ui-control hidden lg:flex bg-[var(--surface-sunken)] px-3.5 text-sm text-text-tertiary items-center gap-2 cursor-pointer border border-transparent hover:border-border-default hover:text-text-secondary"

              aria-label="打开搜索命令面板"
            >
              <Search className="w-4 h-4" aria-hidden="true" />
              <span>搜索…</span>
              <kbd className="font-mono text-xs text-text-tertiary bg-surface border border-border-default rounded px-1.5 py-0.5">⌘K /</kbd>
            </button>
            {/* 移动端搜索图标 */}
            <button
              onClick={openPalette}
                            className="ui-control lg:hidden inline-flex items-center justify-center w-10 p-2 text-text-secondary hover:bg-border-subtle"

              aria-label="搜索"
            >
              <Search className="w-5 h-5" aria-hidden="true" />
            </button>

            {/* #1 桌面端「折叠为仅图标」侧边栏按钮 */}
            <button
              onClick={toggleSidebarCollapsed}
                            className="ui-control hidden md:flex items-center justify-center w-10 p-2 text-text-secondary hover:bg-border-subtle hover:text-brand-500"

              aria-label={sidebarCollapsed ? '展开侧边栏' : '折叠为仅图标'}
              title={sidebarCollapsed ? '展开侧边栏' : '折叠为仅图标'}
            >
              {sidebarCollapsed ? (
                <PanelLeftOpen className="w-5 h-5" aria-hidden="true" />
              ) : (
                <PanelLeftClose className="w-5 h-5" aria-hidden="true" />
              )}
            </button>

            {/* #21 亮/暗主题切换入口 */}
            <button
              onClick={toggleTheme}
              className="ui-control inline-flex w-10 shrink-0 items-center justify-center text-text-secondary hover:bg-border-subtle transition-colors"
              aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
              title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
            >
              {theme === 'dark' ? (
                <Sun className="w-5 h-5" aria-hidden="true" />
              ) : (
                <Moon className="w-5 h-5" aria-hidden="true" />
              )}
            </button>

            {/* 通知 — 下拉面板展示待审批与快捷入口 */}
            <div className="relative">
              <button
                ref={notifTriggerRef}
                aria-haspopup="dialog"
                aria-controls={notifOpen ? 'header-notifications-panel' : undefined}
                onClick={handleNotifToggle}
                className={`ui-control relative inline-flex items-center justify-center w-10 p-2 text-text-secondary hover:bg-border-subtle hover:text-brand-500 ${notifOpen ? 'z-20' : ''}`}

                aria-label={displayCount > 0 ? `通知，${displayCount} 项未读` : '通知'}
                aria-expanded={notifOpen}
              >
                <Bell className="w-5 h-5" aria-hidden="true" />
                {displayCount > 0 && (
                  <span className="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-error text-white text-xs font-bold flex items-center justify-center">
                    {displayCount > 99 ? '99+' : displayCount}
                  </span>
                )}
              </button>

              <AnimatePresence>
                {notifOpen && (
                  <motion.div
                    key="notifications-panel"
                    ref={notifPanelRef}
                    id="header-notifications-panel"
                    role="dialog"
                    aria-label="通知中心"
                    tabIndex={-1}
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.15 }}
                    className="fixed left-3 right-3 top-16 mt-2 max-w-[calc(100vw-1.5rem)] bg-surface rounded-lg border border-border-default py-1 z-50 shadow-lift sm:absolute sm:left-auto sm:top-auto sm:right-0 sm:w-72"
                  >
                      <div className="px-4 py-3 border-b border-border-subtle">
                        <p className="text-sm font-medium text-text-primary">通知中心</p>
                      </div>
                      {approvalsLoading || approvalsError ? (
                        <div className="px-4 py-3" role="status">
                          <p className="text-sm text-text-secondary">{approvalsLoading ? '正在获取审批通知…' : '审批通知暂不可用'}</p>
                          {approvalsError && <Link to="/dashboard" onClick={() => setOpenPopover(null)} className="mt-2 inline-block text-sm text-brand-500 underline">前往驾驶舱查看</Link>}
                        </div>
                      ) : pendingApprovalCount > 0 ? (
                        <Link
                          to="/dashboard"
                          onClick={() => setOpenPopover(null)}
                          className="flex items-start gap-3 px-4 py-3 hover:bg-elevated transition-colors"
                        >
                          <div className="w-8 h-8 rounded-full bg-warning/10 flex items-center justify-center flex-shrink-0">
                            <Bell className="w-4 h-4 text-warning" aria-hidden="true" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-text-primary">
                              {pendingApprovalCount} 项待审批
                            </p>
                            <p className="text-xs text-text-tertiary mt-0.5">
                              前往总览驾驶舱处理
                            </p>
                          </div>
                        </Link>
                      ) : (
                        <div className="flex items-start gap-3 px-4 py-3">
                          <div className="w-8 h-8 rounded-full bg-success/10 flex items-center justify-center flex-shrink-0">
                            <CheckCircle2 className="w-4 h-4 text-success" aria-hidden="true" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-text-primary">暂无待处理事项</p>
                            <p className="text-xs text-text-tertiary mt-0.5">所有审批已处理完毕</p>
                          </div>
                        </div>
                      )}
                      <div className="border-t border-border-subtle mt-1 pt-1">
                        <Link
                          to="/evolution"
                          onClick={() => setOpenPopover(null)}
                          className="flex items-center gap-2 px-4 py-2 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary transition-colors"
                        >
                          <TrendingUp className="w-4 h-4 text-text-tertiary" aria-hidden="true" />
                          查看 AI 优化建议
                        </Link>
                        <Link
                          to="/build?tab=runtime"
                          onClick={() => setOpenPopover(null)}
                          className="flex items-center gap-2 px-4 py-2 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary transition-colors"
                        >
                          <Layers className="w-4 h-4 text-text-tertiary" aria-hidden="true" />
                          查看运行时版本
                        </Link>
                      </div>
                    </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* 历史记录 */}
            <div className="relative">
              <button
                ref={historyTriggerRef}
                onClick={handleHistoryToggle}
                aria-label="历史记录"
                aria-haspopup="dialog"
                aria-expanded={historyOpen}
                aria-controls={historyOpen ? 'header-history-panel' : undefined}
                className={`ui-control relative inline-flex w-10 shrink-0 items-center justify-center text-text-secondary hover:bg-border-subtle transition-colors ${historyOpen ? 'z-20' : ''}`}
              >
                <History className="w-5 h-5" aria-hidden="true" />
              </button>

              <AnimatePresence>
                {historyOpen && (
                  <motion.div
                    key="history-panel"
                    ref={historyPanelRef}
                    id="header-history-panel"
                    role="dialog"
                    aria-label="最近操作"
                    tabIndex={-1}
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.15 }}
                    className="absolute right-0 mt-2 w-64 max-w-[calc(100vw-1.5rem)] bg-surface rounded-lg border border-border-default py-1 z-50 shadow-lift"
                  >
                      <div className="px-4 py-3 border-b border-border-subtle">
                        <p className="text-sm font-medium text-text-primary">最近操作</p>
                      </div>
                      {history.length === 0 ? (
                        <div className="px-4 py-6 text-center">
                          <History className="w-6 h-6 text-text-muted mx-auto mb-2" aria-hidden="true" />
                          <p className="text-xs text-text-tertiary">暂无历史记录</p>
                        </div>
                      ) : (
                        <div className="py-1">
                          {history.map((h) => {
                            const mins = Math.floor((Date.now() - h.time) / 60000)
                            const timeLabel = mins < 1 ? '刚刚' : mins < 60 ? `${mins} 分钟前` : `${Math.floor(mins / 60)} 小时前`
                            return (
                              <Link
                                key={h.path + h.time}
                                to={h.path}
                                onClick={() => setOpenPopover(null)}
                                className="flex items-center gap-3 px-4 py-2.5 hover:bg-elevated transition-colors"
                              >
                                <div className="w-7 h-7 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
                                  <History className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
                                </div>
                                <div className="flex-1 min-w-0">
                                  <p className="text-sm text-text-primary truncate">{h.label}</p>
                                  <p className="text-xs text-text-tertiary">{timeLabel}</p>
                                </div>
                              </Link>
                            )
                          })}
                        </div>
                      )}
                    </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* 用户菜单 */}
            <div className="relative">
              <button
                ref={userTriggerRef}
                onClick={handleUserToggle}
                aria-label="用户菜单"
                aria-haspopup="dialog"
                aria-expanded={userMenuOpen}
                aria-controls={userMenuOpen ? 'header-user-panel' : undefined}
                className={`ui-control relative inline-flex w-10 shrink-0 items-center justify-center hover:bg-border-subtle ${userMenuOpen ? 'z-20' : ''}`}

              >
                <div className="w-6 h-6 rounded-full bg-brand-500 flex items-center justify-center text-white text-xs font-medium">
                  {user?.name?.charAt(0)?.toUpperCase() || 'U'}
                </div>
              </button>

              <AnimatePresence>
                {userMenuOpen && (
                  <motion.div
                    key="user-panel"
                    ref={userPanelRef}
                    id="header-user-panel"
                    role="dialog"
                    aria-label="用户菜单"
                    tabIndex={-1}
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.15 }}
                    className="absolute right-0 mt-2 w-56 max-w-[calc(100vw-1.5rem)] bg-surface rounded-lg border border-border-default py-1 z-50 shadow-lift"
                  >
                      <div className="px-4 py-3 border-b border-border-subtle">
                        <p className="text-sm font-medium text-text-primary">{user?.name || '用户'}</p>
                        <p className="text-xs text-text-tertiary truncate mt-0.5">{user?.email || ''}</p>
                      </div>
                      <Link
                        to="/settings"
                        onClick={() => setOpenPopover(null)}
                        className="flex items-center px-4 py-2 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary transition-colors"
                      >
                        <Settings className="mr-2 h-4 w-4" aria-hidden="true" />
                        账户设置
                      </Link>
                      <button
                        onClick={() => {
                          setOpenPopover(null)
                          handleLogout()
                        }}
                        className="flex items-center w-full text-left px-4 py-2 text-sm text-error hover:bg-error/10 transition-colors"
                      >
                        <LogOut className="mr-2 h-4 w-4" aria-hidden="true" />
                        退出登录
                      </button>
                    </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </header>

        {/* 主内容区 */}
                <main id="main-content" className={fluid ? 'flex-1 flex flex-col min-h-0 overflow-hidden w-full' : 'app-content flex-1 max-w-[88rem] mx-auto w-full px-4 sm:px-6'}>

          {children}
        </main>
      </div>

      {/* ⌘K 全局命令面板 */}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} items={commandItems} />
    </div>
  )
}

// ============================================================
// 侧边栏内容（桌面与移动端共用）
// ============================================================

interface SidebarContentProps {
  user: { name?: string; email?: string; role?: string } | null
  isActive: (path: string) => boolean
  onLogout: () => void
  onNavigate?: () => void
  autonomyMode: ReturnType<typeof useAutonomyMode>
  /** #1 折叠为仅图标（桌面端） */
  collapsed?: boolean
}

function SidebarContent({ user, isActive, onNavigate, autonomyMode, collapsed = false }: SidebarContentProps) {
  return (
    <>
      {/* 品牌 Logo */}
      <Link
        to="/"
        onClick={onNavigate}
        title="AutoTeams"
        className={`text-base font-semibold tracking-[-0.025em] text-brand-500 flex-shrink-0 flex items-center gap-2 h-16 border-b border-border-default ${
          collapsed ? 'justify-center px-0' : 'px-5'
        }`}
      >
        <Logo className="w-5 h-5 flex-shrink-0" variant="brand" />
        {!collapsed && 'AutoTeams'}
      </Link>

      {/* 导航分组（纵向滚动） */}
            <nav className="flex-1 overflow-y-auto py-5 px-3" aria-label="主导航">

        {navGroups.map((group) => {
          const visibleItems = group.items.filter(
            (item) => !item.adminOnly || user?.role === 'admin',
          ).filter(
            (item) => isNavVisibleForMode(autonomyMode, item.path),
          )
          if (visibleItems.length === 0) return null
          return (
            <div key={group.id} className={collapsed ? 'mb-3' : 'mb-4'}>
              {!collapsed && (
                                <p className="px-3 mb-1.5 text-[11px] font-semibold tracking-[0.04em] text-text-muted">

                  {group.label}
                </p>
              )}
              <div className="space-y-0.5">
                {visibleItems.map((item) => {
                  const Icon = item.icon
                  const active = isActive(item.path)
                  return (
                    <Link
                      key={item.path}
                      to={item.path}
                      onClick={onNavigate}
                      title={item.label}
                                            className={`ui-nav-item flex items-center ${
                        collapsed ? 'justify-center mx-auto w-10' : 'gap-2.5 px-3 text-[14px]'
                      } ${
                        active
                          ? 'ui-nav-item--active'
                          : 'text-[#6B6B66] font-normal hover:text-[#0B0B0B] hover:bg-black/[0.03]'
                      }`}
                    >
                      <Icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
                      {!collapsed && <span className="truncate">{item.label}</span>}
                      {!collapsed && active && (
                        <ChevronRight className="w-3.5 h-3.5 ml-auto flex-shrink-0" aria-hidden="true" />
                      )}
                    </Link>
                  )
                })}
              </div>
            </div>
          )
        })}
      </nav>

      {/* 底部：用户信息 */}
            <div className="flex-shrink-0 p-3 border-t border-border-default bg-white/30">

        <div className={`flex items-center gap-2 ${collapsed ? 'justify-center px-0' : 'px-2 py-1.5'}`}>
          <div
            className="w-6 h-6 rounded-full bg-brand-500 flex items-center justify-center text-white text-xs font-medium flex-shrink-0"
            title={user?.name || '用户'}
          >
            {user?.name?.charAt(0)?.toUpperCase() || 'U'}
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="text-sm font-medium text-text-primary truncate">{user?.name || '用户'}</p>
              <p className="text-xs text-text-tertiary truncate">{user?.email || ''}</p>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
