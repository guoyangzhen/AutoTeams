import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { ArrowRight, Check, ChevronRight, Compass, Play, Sparkles, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { CompanyJourneyState } from '@/utils/productAnalytics'
import {
  trackCompanyProductEvent,
  trackCompanyProductEventOnce,
} from '@/utils/productAnalytics'

const STORAGE_KEY = 'autoteams_company_value_tour_v1'

const TOUR_STEPS = [
  {
    eyebrow: '第 1 步 · 建立共同语境',
    title: '把企业知识变成可运行的底座',
    description: '资料并非只是被上传和存放。完成编译后，组织、流程、能力与知识会组成可版本化的 Enterprise Runtime。',
    outcome: '你会得到：一套可被 AI 团队共同理解、持续更新的企业运行模型。',
  },
  {
    eyebrow: '第 2 步 · 组建可信团队',
    title: '让岗位能力成为可协作的 AI 员工',
    description: '系统根据运行时匹配岗位、知识、工具和生命周期，让每名 AI 员工在明确边界内承担工作。',
    outcome: '你会得到：有职责、可追踪、可治理的数字员工团队。',
  },
  {
    eyebrow: '第 3 步 · 让流程真正运转',
    title: '用事件链路观察协作，而不是猜测',
    description: '询盘、报价、审批、同步和售后会形成可阅读的任务流。需要人判断的节点不会被自动跳过。',
    outcome: '你会得到：可介入、可审计、可恢复的 AI 协作过程。',
  },
  {
    eyebrow: '第 4 步 · 验证经营结果',
    title: '把自动化投入连接到业务价值',
    description: '系统基于真实运行指标汇总自动化率、替代人时、成本节约与 ROI，不用演示数字填满仪表盘。',
    outcome: '你会得到：一条从知识到结果、可以持续优化的企业 AI 价值路径。',
  },
] as const

function readTourStatus(): 'completed' | 'dismissed' | null {
  if (typeof window === 'undefined') return null
  const value = window.localStorage.getItem(STORAGE_KEY)
  return value === 'completed' || value === 'dismissed' ? value : null
}

function saveTourStatus(status: 'completed' | 'dismissed'): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(STORAGE_KEY, status)
}

interface CompanyValueTourProps {
  journeyState: CompanyJourneyState
  onStartRecommendedAction: () => void
  recommendedActionLabel: string
  actionIsRunning: boolean
}

/**
 * 不遮挡主工作流的嵌入式导览。它只在该浏览器首次访问时自动展开，
 * 完成或跳过后保持低噪声入口，用户可随时重新浏览。
 */
export function CompanyValueTour({
  journeyState,
  onStartRecommendedAction,
  recommendedActionLabel,
  actionIsRunning,
}: CompanyValueTourProps) {
  const reduceMotion = useReducedMotion()
  const [open, setOpen] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)
  const viewedStepRef = useRef<string | null>(null)

  useEffect(() => {
    if (readTourStatus()) return
    setOpen(true)
    trackCompanyProductEventOnce('company-value-tour-started', 'company_tour_started', {
      journey_state: journeyState,
      tour_step: 1,
    })
  }, [journeyState])

  useEffect(() => {
    if (!open) return
    const key = `${stepIndex}:${journeyState}`
    if (viewedStepRef.current === key) return
    viewedStepRef.current = key
    trackCompanyProductEvent('company_tour_step_viewed', {
      journey_state: journeyState,
      tour_step: stepIndex + 1,
    })
  }, [journeyState, open, stepIndex])

  const restart = () => {
    setStepIndex(0)
    viewedStepRef.current = null
    setOpen(true)
    trackCompanyProductEvent('company_tour_restarted', {
      journey_state: journeyState,
      tour_step: 1,
    })
  }

  const skip = () => {
    saveTourStatus('dismissed')
    setOpen(false)
    trackCompanyProductEvent('company_tour_skipped', {
      journey_state: journeyState,
      tour_step: stepIndex + 1,
    })
  }

  const next = () => {
    if (stepIndex < TOUR_STEPS.length - 1) {
      setStepIndex((current) => current + 1)
      return
    }
    saveTourStatus('completed')
    setOpen(false)
    trackCompanyProductEvent('company_tour_completed', {
      journey_state: journeyState,
      tour_step: TOUR_STEPS.length,
    })
  }

  const step = TOUR_STEPS[stepIndex]

  if (!open) {
    return (
      <button
        type="button"
        onClick={restart}
        className="group inline-flex min-h-10 items-center gap-2 rounded-xl border border-dashed border-border-default bg-[var(--surface-raised)] px-3 text-sm font-medium text-text-secondary transition-colors hover:border-brand-300 hover:bg-[var(--surface-tint)] hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        <Compass className="h-4 w-4 text-brand-500" aria-hidden="true" />
        了解 AutoTeams 的价值路径
        <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
      </button>
    )
  }

  return (
    <section
      aria-labelledby="company-value-tour-title"
      className="relative overflow-hidden rounded-2xl border border-brand-500/15 bg-[linear-gradient(135deg,var(--surface-raised),color-mix(in_srgb,var(--surface-tint)_86%,transparent))] shadow-[0_14px_34px_rgba(27,42,63,0.06)]"
    >
      <div className="absolute right-0 top-0 h-28 w-28 rounded-full bg-brand-500/5 blur-2xl" aria-hidden="true" />
      <div className="relative p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-brand-500 text-white shadow-sm">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </span>
            <div>
              <p className="ui-context-kicker text-brand-600">首次导览 · 从知识到结果</p>
              <h2 id="company-value-tour-title" className="mt-0.5 text-sm font-semibold text-text-primary">用 60 秒理解系统如何创造经营价值</h2>
            </div>
          </div>
          <button
            type="button"
            onClick={skip}
            className="inline-flex min-h-9 items-center gap-1 rounded-lg px-2 text-xs font-medium text-text-tertiary transition-colors hover:bg-[var(--surface-sunken)] hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            aria-label="暂时跳过价值路径导览"
          >
            稍后再看
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-5 flex gap-1.5" aria-label={`导览进度：第 ${stepIndex + 1} 步，共 ${TOUR_STEPS.length} 步`}>
          {TOUR_STEPS.map((item, index) => (
            <span
              key={item.title}
              className={`h-1.5 flex-1 rounded-full transition-colors ${index <= stepIndex ? 'bg-brand-500' : 'bg-border-default'}`}
              aria-hidden="true"
            />
          ))}
        </div>

        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={step.title}
            initial={reduceMotion ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? undefined : { opacity: 0, y: -6 }}
            transition={{ duration: reduceMotion ? 0 : 0.22, ease: 'easeOut' }}
            className="mt-5"
          >
            <p className="ui-context-kicker">{step.eyebrow}</p>
            <h3 className="mt-1.5 text-xl font-semibold tracking-[-0.03em] text-text-primary">{step.title}</h3>
            <p className="ui-page-description mt-2 max-w-3xl text-sm">{step.description}</p>
            <p className="mt-3 flex items-start gap-2 rounded-xl border border-success/15 bg-success/5 px-3 py-2 text-xs leading-relaxed text-text-secondary">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
              {step.outcome}
            </p>
          </motion.div>
        </AnimatePresence>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          <span className="text-xs text-text-tertiary">第 {stepIndex + 1} / {TOUR_STEPS.length} 步</span>
          <div className="flex flex-wrap gap-2">
            {stepIndex === TOUR_STEPS.length - 1 && (
              <Button
                variant="outline"
                size="sm"
                onClick={onStartRecommendedAction}
                disabled={actionIsRunning}
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {recommendedActionLabel}
              </Button>
            )}
            <Button variant="primary" size="sm" onClick={next}>
              {stepIndex === TOUR_STEPS.length - 1 ? '完成导览' : '继续'}
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
        </div>
      </div>
    </section>
  )
}
