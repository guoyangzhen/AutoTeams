/**
 * HubPage — 页面合并通用容器（P5.2）。
 *
 * 一个 hub 聚合多个子页面，通过顶部 SubTabBar 切换：
 * - 子页面通过 EmbeddedProvider 嵌入，其自带 <Layout> 退化为透传，
 *   避免双重侧边栏（子页面代码零改动）。
 * - 标签状态通过 URL ?tab=xxx 同步，支持深链与刷新保持。
 * - 子页面懒加载，各自带 Suspense 边界。
 *
 * 用于：企业构建(编译+运行时)、AI员工(员工+模板)、智能体(编排+知识+技能)、
 * 总览(今日公司+业务效果)。
 */
import { Suspense, lazy, useMemo, type ComponentType } from 'react'
import { Activity } from 'lucide-react'

import { useSearchParams } from 'react-router-dom'
import Layout, { EmbeddedProvider } from '@/components/Layout'
import { SubTabBar, type SubTabItem } from '@/components/ui/SubTabBar'
import { Spinner } from '@/components/ui/Spinner'

export interface HubTab extends SubTabItem {
  /** 懒加载的子页面组件 */
  component: () => Promise<{ default: ComponentType }>
  /** 是否需要 fluid 布局（如画布类全宽页面） */
  fluid?: boolean
}

interface HubPageProps {
  tabs: HubTab[]
  /** 默认 tab key（?tab 缺失时使用） */
  defaultTab: string
}

const HUB_COPY: Record<string, { eyebrow: string; title: string; description: string }> = {
  '/company': {
    eyebrow: '企业运行总览',
    title: '看清现在，决定下一步',
    description: '把任务流、审批、协作信号和经营反馈聚合为一个可行动的企业视图。',
  },
  '/build': {
    eyebrow: '企业构建',
    title: '让企业能力持续可运行',
    description: '在这里完成知识编译、运行时校验与版本追踪，所有长任务都有可恢复的状态。',
  },
  '/employees': {
    eyebrow: 'AI 团队',
    title: '组织可用的 AI 能力',
    description: '创建、配置并观察 AI 员工，让团队角色、协作方式与能力模板保持清晰。',
  },
  '/agents': {
    eyebrow: '知识中枢',
    title: '把知识变成可调用能力',
    description: '管理智能体、知识库与技能资产，保持每项能力的来源、范围和生命周期可见。',
  },
}

function TabFallback() {
  return (
    <div className="flex items-center justify-center py-20">
      <Spinner size="lg" />
    </div>
  )
}

export function HubPage({ tabs, defaultTab }: HubPageProps) {
  const [searchParams] = useSearchParams()
  const activeKey = searchParams.get('tab') || defaultTab
  const activeTab = tabs.find((t) => t.key === activeKey) || tabs[0]

  // 预创建各 tab 的 lazy 组件（稳定引用，避免每次 render 重建导致子树 remount）
  const lazyComponents = useMemo(() => {
    const map: Record<string, ComponentType> = {}
    tabs.forEach((t) => {
      map[t.key] = lazy(t.component)
    })
    return map
  }, [tabs])
    const ActiveComponent = lazyComponents[activeTab.key]
  const hubPath = tabs[0]?.to.split('?')[0] || ''
  const copy = HUB_COPY[hubPath] || {
    eyebrow: 'AutoTeams 工作空间',
    title: activeTab.label,
    description: '在一个明确的工作域内完成当前任务，并随时了解系统状态。',
  }

  // SubTabBar 用 Link to 切换，?tab= 同步到 URL

  const tabBarItems: SubTabItem[] = tabs.map((t) => ({
    key: t.key,
    label: t.label,
    icon: t.icon,
    to: t.to,
  }))

  return (
    <Layout fluid={activeTab.fluid}>
            <section className="pt-2 pb-2 sm:pt-4" aria-labelledby="hub-page-title">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <p className="ui-context-kicker">{copy.eyebrow}</p>
            <h1 id="hub-page-title" className="ui-page-title mt-2">{copy.title}</h1>
            <p className="ui-page-description mt-3">{copy.description}</p>
          </div>
          <div className="inline-flex w-fit items-center gap-2 rounded-full border border-border-default bg-[var(--surface-tint)] px-3 py-2 text-xs font-medium text-text-secondary">
            <Activity className="h-3.5 w-3.5 text-success" aria-hidden="true" />
            <span>工作域已就绪</span>
          </div>
        </div>
      </section>
      <SubTabBar tabs={tabBarItems} activeKey={activeTab.key} />
      <div className={activeTab.fluid ? 'flex-1 flex flex-col min-h-0' : 'w-full'}>

        <EmbeddedProvider>
          <Suspense fallback={<TabFallback />}>
            <ActiveComponent />
          </Suspense>
        </EmbeddedProvider>
      </div>
    </Layout>
  )
}

export default HubPage
