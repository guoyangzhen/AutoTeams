/**
 * EvolutionWorkspace — 组织进化与反事实推演核心工作台（AutoTeams 5.0）。
 *
 * 对应 Screens 10 & 11：
 * - Tab 1: 【组织进化飞轮】（影子考核 4 步水平轨道、知识缺口 3 项高频归因、Top 3 绩效榜、组织版本与回滚）
 * - Tab 2: 【反事实推演】（双盲影子推演差分、免干预自晋升准入看板）
 * - Tab 3: 【全链路 Trace】（深邃暗色瀑布流、Span 调用链展开、¥0.008/1k 真实成本测算与 HMAC 审计链校验）
 */
import { lazy, Suspense, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TrendingUp, Scale, Activity, Dna } from 'lucide-react'
import Layout, { EmbeddedProvider } from '@/components/Layout'
import { SubTabBar } from '@/components/ui/SubTabBar'
import { Spinner } from '@/components/ui/Spinner'

const EvolutionPage = lazy(() => import('@/pages/EvolutionPage'))
const CounterfactualArena = lazy(() => import('@/pages/CounterfactualArena'))
const TraceExplorer = lazy(() => import('@/pages/TraceExplorer'))

const TABS: Array<{
  key: string
  label: string
  icon: typeof TrendingUp
  to: string
}> = [
  {
    key: 'flywheel',
    label: '组织进化飞轮',
    icon: TrendingUp,
    to: '/evolution?tab=flywheel',
  },
  {
    key: 'counterfactual',
    label: '反事实推演',
    icon: Scale,
    to: '/evolution?tab=counterfactual',
  },
  {
    key: 'trace',
    label: '全链路 Trace',
    icon: Activity,
    to: '/evolution?tab=trace',
  },
]

export default function EvolutionWorkspace() {
  const [searchParams] = useSearchParams()
  const activeKey = searchParams.get('tab') || 'flywheel'

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
              <span className="font-meta text-meta text-text-main font-medium">组织进化与推演</span>
            </div>
            <h1 className="font-headline-lg text-headline-lg font-bold tracking-tight text-text-main mt-1">
              组织进化与反事实推演
            </h1>
            <p className="font-body-sm text-body-sm text-text-muted mt-1">
              影子考核自晋升 · 双盲反事实差分评测 · 全链路时序 Trace 与审计校验
            </p>
          </div>

          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] border border-border-hairline bg-card text-xs text-text-muted">
              <Dna className="w-3.5 h-3.5" aria-hidden="true" />
              <span>第 36 周 · 平均影子对齐度 92.6%</span>
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
                  <p className="text-sm text-text-muted">正在载入进化工作台…</p>
                </div>
              }
            >
              {activeTab.key === 'flywheel' && <EvolutionPage />}
              {activeTab.key === 'counterfactual' && <CounterfactualArena />}
              {activeTab.key === 'trace' && <TraceExplorer />}
            </Suspense>
          </EmbeddedProvider>
        </main>
      </div>
    </Layout>
  )
}
