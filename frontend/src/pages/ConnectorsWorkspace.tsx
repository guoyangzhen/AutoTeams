/**
 * ConnectorsWorkspace — 连接中枢与执行器核心工作台（AutoTeams 5.0）。
 *
 * 对应 Screens 08, 09 与 Local Runner 2.0：
 * - Tab 1: 【全渠道网关】（企微/飞书接入账号卡片、右侧抽屉式 Webhook 回调模拟器，可真实发送测试消息）
 * - Tab 2: 【工具生态 & MCP】（标准 MCP stdio/sse 工具列表、探针状态）
 * - Tab 3: 【Local Runner 2.0】（端侧物理执行器监视视窗、在线探针状态、双因子 2FA 安全确认模态窗）
 */
import { lazy, Suspense, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Smartphone, Layers, Monitor } from 'lucide-react'
import Layout, { EmbeddedProvider } from '@/components/Layout'
import { SubTabBar } from '@/components/ui/SubTabBar'
import { Spinner } from '@/components/ui/Spinner'

const ChannelManagement = lazy(() => import('@/pages/ChannelManagement'))
const ToolEcosystem = lazy(() => import('@/pages/ToolEcosystem'))
const LocalRunnerStudio = lazy(() => import('@/pages/LocalRunnerStudio'))

const TABS: Array<{
  key: string
  label: string
  icon: typeof Smartphone
  to: string
}> = [
  {
    key: 'gateway',
    label: '全渠道网关',
    icon: Smartphone,
    to: '/connectors?tab=gateway',
  },
  {
    key: 'mcp',
    label: '工具生态 & MCP',
    icon: Layers,
    to: '/connectors?tab=mcp',
  },
  {
    key: 'runner',
    label: 'Local Runner 2.0',
    icon: Monitor,
    to: '/connectors?tab=runner',
  },
]

export default function ConnectorsWorkspace() {
  const [searchParams] = useSearchParams()
  const activeKey = searchParams.get('tab') || 'gateway'

  const activeTab = useMemo(() => {
    return TABS.find((t) => t.key === activeKey) || TABS[0]
  }, [activeKey])

  return (
    <Layout>
      <div className="flex flex-col w-full min-h-full bg-paper">
        {/* Workspace Header */}
        <header className="px-6 sm:px-10 pt-6 pb-2 border-b border-border-hairline bg-paper flex flex-col sm:flex-row sm:items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-meta text-meta text-text-muted">核心工作台</span>
              <span className="text-border-hairline">/</span>
              <span className="font-meta text-meta text-text-main font-medium">连接中枢与执行器</span>
            </div>
            <h1 className="font-headline-lg text-headline-lg font-bold tracking-tight text-text-main mt-1">
              连接中枢与执行器
            </h1>
            <p className="font-body-sm text-body-sm text-text-muted mt-1">
              全渠道接入中枢 · MCP 工具协议生态 · Local Runner 具身端侧物理执行器
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] border border-border-hairline bg-card text-xs text-text-muted">
              <span className="at-dot at-dot-ok" aria-hidden="true" />
              <span>通信专线与探针在线</span>
            </span>
          </div>
        </header>

        {/* SubTabBar Navigation */}
        <div className="px-6 sm:px-10 bg-paper">
          <SubTabBar tabs={TABS} activeKey={activeTab.key} />
        </div>

        {/* Tab Content Canvas */}
        <main className="flex-1 w-full min-h-0 flex flex-col">
          <EmbeddedProvider>
            <Suspense
              fallback={
                <div className="flex flex-col items-center justify-center gap-3 py-24" role="status">
                  <Spinner size="md" />
                  <p className="text-sm text-text-muted">正在载入连接中枢…</p>
                </div>
              }
            >
              {activeTab.key === 'gateway' && <ChannelManagement />}
              {activeTab.key === 'mcp' && <ToolEcosystem />}
              {activeTab.key === 'runner' && <LocalRunnerStudio />}
            </Suspense>
          </EmbeddedProvider>
        </main>
      </div>
    </Layout>
  )
}
