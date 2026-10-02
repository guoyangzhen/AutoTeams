/**
 * TeamBoardWorkspace — 团队看板与特遣队协同工作台（AutoTeams 5.0）。
 *
 * 对应 Screens 06 & 07：
 * - 视图 1：多列团队任务看板（待分派 / 竞标中 / 执行中 / 待验收 / 已收口）+ 共享黑板信息流
 * - 视图 2：5.0 动态敏捷特遣队与带资竞标（蜂群自治协商、HP 质押对赌、DAG 子任务链）
 */
import { useState, lazy, Suspense } from 'react'
import { Kanban, Target } from 'lucide-react'
import { EmbeddedProvider } from '@/components/Layout'
import { Spinner } from '@/components/ui/Spinner'

const WorkgroupKanban = lazy(() => import('@/pages/WorkgroupKanban'))
const StrikeTeamsPage = lazy(() => import('@/pages/StrikeTeamsPage'))

export default function TeamBoardWorkspace() {
  const [subView, setSubView] = useState<'kanban' | 'strike'>('kanban')

  return (
    <div className="w-full flex flex-col">
      {/* 顶部次级视图切换 */}
      <div className="px-6 py-3 border-b border-border-hairline bg-paper/60 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-headline-md text-headline-md font-semibold text-text-main">
            {subView === 'kanban' ? '团队协同看板与黑板' : '5.0 动态敏捷特遣队与带资竞标'}
          </span>
          <span className="font-meta text-meta text-text-muted hidden sm:inline">
            {subView === 'kanban'
              ? '多列矩阵流转 · 共享黑板信息流协同'
              : '蜂群自治协商 · HP 算力抵押 · Contract Net 协议'}
          </span>
        </div>

        <div role="tablist" aria-label="切换团队协同视图" className="at-seg">
          <button
            type="button"
            role="tab"
            aria-selected={subView === 'kanban'}
            onClick={() => setSubView('kanban')}
            className={`at-seg-item flex items-center gap-1.5 text-xs ${
              subView === 'kanban' ? 'font-semibold text-text-main' : 'text-text-muted'
            }`}
          >
            <Kanban className="w-3.5 h-3.5" aria-hidden="true" />
            <span>任务看板与黑板</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={subView === 'strike'}
            onClick={() => setSubView('strike')}
            className={`at-seg-item flex items-center gap-1.5 text-xs ${
              subView === 'strike' ? 'font-semibold text-text-main' : 'text-text-muted'
            }`}
          >
            <Target className="w-3.5 h-3.5" aria-hidden="true" />
            <span>特遣队与带资竞标</span>
          </button>
        </div>
      </div>

      {/* 主工作区 */}
      <div className="flex-1 w-full min-h-0">
        <EmbeddedProvider>
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-20">
                <Spinner size="lg" />
              </div>
            }
          >
            {subView === 'kanban' ? <WorkgroupKanban /> : <StrikeTeamsPage />}
          </Suspense>
        </EmbeddedProvider>
      </div>
    </div>
  )
}
