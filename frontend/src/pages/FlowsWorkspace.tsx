/**
 * FlowsWorkspace — 业务规程与团队协同核心工作台（AutoTeams 5.0）。
 *
 * 对应 Screens 05, 06, 07：
 * - Tab 1: 【SOP 规程卡编排】（白底发丝线卡片节点、贝塞尔曲线、右侧抽屉配置 Tri-Rule 三重安全护栏，支持快速仿真推进步骤）
 * - Tab 2: 【团队看板与特遣队】（多列团队状态看板、共享黑板信息流、5.0 动态敏捷特遣队与带资竞标）
 */
import { lazy, Suspense, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Workflow, Kanban, Sparkles } from 'lucide-react'
import Layout, { EmbeddedProvider } from '@/components/Layout'
import { SubTabBar } from '@/components/ui/SubTabBar'
import { Spinner } from '@/components/ui/Spinner'

const FlowEditorPage = lazy(() => import('@/pages/FlowEditorPage'))
const TeamBoardWorkspace = lazy(() => import('@/pages/TeamBoardWorkspace'))

const TABS: Array<{
  key: string
  label: string
  icon: typeof Workflow
  to: string
}> = [
  {
    key: 'sop',
    label: 'SOP 规程卡编排',
    icon: Workflow,
    to: '/flows?tab=sop',
  },
  {
    key: 'team',
    label: '团队看板与特遣队',
    icon: Kanban,
    to: '/flows?tab=team',
  },
]

export default function FlowsWorkspace() {
  const [searchParams] = useSearchParams()
  const activeKey = searchParams.get('tab') || 'sop'

  const activeTab = useMemo(() => {
    return TABS.find((t) => t.key === activeKey) || TABS[0]
  }, [activeKey])

  return (
    <Layout fluid={activeTab.key === 'sop'}>
      <div className="flex flex-col w-full min-h-full bg-paper">
        {/* Workspace Header */}
        <header className="px-6 sm:px-10 pt-6 pb-2 border-b border-border-hairline bg-paper flex flex-col sm:flex-row sm:items-end justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-meta text-meta text-text-muted">核心工作台</span>
              <span className="text-border-hairline">/</span>
              <span className="font-meta text-meta text-text-main font-medium">业务规程与协同</span>
            </div>
            <h1 className="font-headline-lg text-headline-lg font-bold tracking-tight text-text-main mt-1">
              业务规程与团队协同
            </h1>
            <p className="font-body-sm text-body-sm text-text-muted mt-1">
              状态机 SOP 编排 · 三重安全护栏 · 动态敏捷特遣队与带资竞标
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] border border-border-hairline bg-card text-xs text-text-muted">
              <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
              <span>AutoTeams 5.0 运行时就绪</span>
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
                  <p className="text-sm text-text-muted">正在载入工作台…</p>
                </div>
              }
            >
              {activeTab.key === 'sop' ? <FlowEditorPage /> : <TeamBoardWorkspace />}
            </Suspense>
          </EmbeddedProvider>
        </main>
      </div>
    </Layout>
  )
}
