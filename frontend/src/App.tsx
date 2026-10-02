import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from '@/lib/queryClient'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Toaster, toast } from 'sonner'
import { lazy, Suspense, ReactNode, useEffect } from 'react'
import { AuthProvider, useAuth } from '@/hooks/useAuth'
import { ThemeProvider, useTheme } from '@/hooks/useTheme'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { APP_ERROR_EVENT, type AppErrorEventDetail } from '@/utils/errors'

// P1-04-B: 路由级 lazy import，降低首屏体积
const Login = lazy(() => import('@/pages/Login'))
const Register = lazy(() => import('@/pages/Register'))
const Invite = lazy(() => import('@/pages/Invite'))
const Setup = lazy(() => import('@/pages/Setup'))
const Process = lazy(() => import('@/pages/Process'))
const Chat = lazy(() => import('@/pages/Chat'))
const SkillPage = lazy(() => import('@/pages/SkillPage'))
const Settings = lazy(() => import('@/pages/Settings'))
const LoopDashboard = lazy(() => import('@/pages/LoopDashboard'))
const AgentCanvasPage = lazy(() => import('@/pages/AgentCanvasPage'))
const AuditLogs = lazy(() => import('@/pages/AuditLogs'))
const NotFound = lazy(() => import('@/pages/NotFound'))

// WT5: 新增 PRD §6 页面路由（懒加载）
const BossDashboard = lazy(() => import('@/pages/BossDashboard'))
const InterviewPage = lazy(() => import('@/pages/InterviewPage'))
const WorkExecutionPage = lazy(() => import('@/pages/WorkExecutionPage'))


// AutoTeams 4.0: 核心全栈功能页面（懒加载）
const WorkforceGallery = lazy(() => import('@/pages/WorkforceGallery'))
const WorkforceProfile = lazy(() => import('@/pages/WorkforceProfile'))

// AutoTeams 5.0: 5 大高阶工作空间（懒加载）
const FlowsWorkspace = lazy(() => import('@/pages/FlowsWorkspace'))
const ConnectorsWorkspace = lazy(() => import('@/pages/ConnectorsWorkspace'))
const EvolutionWorkspace = lazy(() => import('@/pages/EvolutionWorkspace'))

// AutoTeams 重构收敛（方案 §4.3.1）：9 大核心工作空间
const ExternalAgentsPage = lazy(() => import('@/pages/ExternalAgentsPage'))
const WorkflowsPage = lazy(() => import('@/pages/WorkflowsPage'))
const AnalyticsPage = lazy(() => import('@/pages/AnalyticsPage'))

function LoadingFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas">
      <div className="text-center">
        <div className="skeleton h-12 w-12 rounded-full mx-auto mb-4"></div>
        <p className="text-text-secondary">加载中...</p>
      </div>
    </div>
  )
}

/**
 * P1-04-D: 路由级 ErrorBoundary + Suspense 包装器。
 *
 * 每个路由元素独立包裹一层 ErrorBoundary 与 Suspense，使单个 lazy-loaded
 * 页面组件抛出的未捕获异常只 unmount 该路由子树，而非整个 <Routes>。
 * 用户可在不刷新整个应用的前提下，通过路由切换或"重试"恢复。
 */
function RouteBoundary({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary>
      <Suspense fallback={<LoadingFallback />}>
        {children}
      </Suspense>
    </ErrorBoundary>
  )
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return <LoadingFallback />
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function PublicRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return <LoadingFallback />
  }

  if (isAuthenticated) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

function AppRoutes() {
  // P1-04-D: 每个路由元素独立包裹 RouteBoundary（ErrorBoundary + Suspense），
  // 单页错误只 unmount 该路由子树，避免整个 <Routes> 崩溃。
  return (
    <Routes>
      <Route path="/login" element={
        <PublicRoute>
          <RouteBoundary>
            <Login />
          </RouteBoundary>
        </PublicRoute>
      } />
      <Route path="/register" element={
        <PublicRoute>
          <RouteBoundary>
            <Register />
          </RouteBoundary>
        </PublicRoute>
      } />
      <Route path="/invite/:token" element={
        <PublicRoute>
          <RouteBoundary>
            <Invite />
          </RouteBoundary>
        </PublicRoute>
      } />
      <Route path="/" element={
        <ProtectedRoute>
          <RouteBoundary>
            <BossDashboard />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/setup" element={
        <ProtectedRoute>
          <RouteBoundary>
            <Setup />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/process/:taskId" element={
        <ProtectedRoute>
          <RouteBoundary>
            <Process />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/chat/:agentId" element={
        <ProtectedRoute>
          <RouteBoundary>
            <Chat />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/skill/:skillId" element={
        <ProtectedRoute>
          <RouteBoundary>
            <SkillPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/settings" element={
        <ProtectedRoute>
          <RouteBoundary>
            <Settings />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/loop/:agentId" element={
        <ProtectedRoute>
          <RouteBoundary>
            <LoopDashboard />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/business-dashboard" element={<Navigate to="/dashboard" replace />} />
      <Route path="/knowledge" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/canvas" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/canvas/:threadId" element={
        <ProtectedRoute>
          <RouteBoundary>
            <AgentCanvasPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/template-gallery" element={<Navigate to="/workforce" replace />} />
      <Route path="/audit-logs" element={
        <ProtectedRoute>
          <RouteBoundary>
            <AuditLogs />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/shadow-mode-demo" element={<Navigate to="/evolution?tab=flywheel" replace />} />
      <Route path="/shadow-counterfactual" element={<Navigate to="/evolution?tab=counterfactual" replace />} />
      <Route path="/skill-management" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/company" element={<Navigate to="/dashboard" replace />} />
      <Route path="/build" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/employees" element={<Navigate to="/workforce" replace />} />
      <Route path="/agents" element={
        <ProtectedRoute>
          <RouteBoundary>
            <ExternalAgentsPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/workflows" element={
        <ProtectedRoute>
          <RouteBoundary>
            <WorkflowsPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/analytics" element={
        <ProtectedRoute>
          <RouteBoundary>
            <AnalyticsPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/compile" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/runtime" element={<Navigate to="/flows?tab=sop" replace />} />

      {/* ============================================================ */}
      {/* AutoTeams 5.0: 5 大高阶核心业务工作空间路由 */}
      {/* ============================================================ */}
      {/* 1. 🏛️ 总览驾驶舱 */}
      <Route path="/dashboard" element={
        <ProtectedRoute>
          <RouteBoundary>
            <BossDashboard />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      {/* 2. 👥 数字员工花名册与档案 */}
      <Route path="/workforce" element={
        <ProtectedRoute>
          <RouteBoundary>
            <WorkforceGallery />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      {/* 3. ⚡ 业务规程与团队协同 */}
      <Route path="/flows" element={
        <ProtectedRoute>
          <RouteBoundary>
            <FlowsWorkspace />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      {/* 4. 🔌 连接中枢与执行器 */}
      <Route path="/connectors" element={
        <ProtectedRoute>
          <RouteBoundary>
            <ConnectorsWorkspace />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      {/* 5. 🧬 组织进化与反事实推演 */}
      <Route path="/evolution" element={
        <ProtectedRoute>
          <RouteBoundary>
            <EvolutionWorkspace />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/interview" element={
        <ProtectedRoute>
          <RouteBoundary>
            <InterviewPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/work-execution" element={
        <ProtectedRoute>
          <RouteBoundary>
            <WorkExecutionPage />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      {/* AutoTeams 4.0 核心能力路由 */}
      <Route path="/workforce-gallery" element={<Navigate to="/workforce" replace />} />
      <Route path="/workforce/profile/:id" element={
        <ProtectedRoute>
          <RouteBoundary>
            <WorkforceProfile />
          </RouteBoundary>
        </ProtectedRoute>
      } />
      <Route path="/flow-editor" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/workgroup-kanban" element={<Navigate to="/flows?tab=team" replace />} />
      <Route path="/bidding-arena" element={<Navigate to="/flows?tab=team" replace />} />
      <Route path="/channel-management" element={<Navigate to="/connectors?tab=gateway" replace />} />
      <Route path="/tools" element={<Navigate to="/connectors?tab=mcp" replace />} />
      <Route path="/traces" element={<Navigate to="/evolution?tab=trace" replace />} />
      <Route path="/strike-teams" element={<Navigate to="/flows?tab=team" replace />} />
      <Route path="/cognitive-memory" element={<Navigate to="/flows?tab=sop" replace />} />
      <Route path="/runner-studio" element={<Navigate to="/connectors?tab=runner" replace />} />
      <Route path="*" element={
        <RouteBoundary>
          <NotFound />
        </RouteBoundary>
      } />
    </Routes>
  )
}

function ThemedToaster() {
  const { theme } = useTheme()
  return (
    <Toaster
      position="top-right"
      offset={{ top: 80, right: 24 }}
      mobileOffset={{ top: 80, left: 12, right: 12 }}
      closeButton
      theme={theme}
      toastOptions={{
        closeButtonAriaLabel: '关闭提示',
        style: {
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border-default)',
          color: 'var(--text-primary)',
        },
      }}
    />
  )
}

/** P1-FE: 监听全局 app-error 事件并弹出 toast */
function GlobalErrorToaster() {
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<AppErrorEventDetail>).detail
      if (!detail) return
      const { message, type = 'error' } = detail
      if (type === 'warning') {
        toast.warning(message)
      } else if (type === 'info') {
        toast.info(message)
      } else {
        toast.error(message)
      }
    }
    window.addEventListener(APP_ERROR_EVENT, handler)
    return () => window.removeEventListener(APP_ERROR_EVENT, handler)
  }, [])
  return null
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <AppRoutes />
          <ThemedToaster />
          <GlobalErrorToaster />
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
