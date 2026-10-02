/**
 * CompilerAnimation — 五级编译动画组件（v2）。
 *
 * 逐级展示五级编译器（PRD §4.3）的编译过程：
 *   Information → Knowledge → Process → Capability → Runtime
 *
 * 升级要点（v2）：
 * - InlineTabs 切换两种视图：「时间线」(垂直，详尽) / 「流程图」(横向 FlowChain，概览)
 * - 横向流程图对标 prototype 部署流水线 5 阶段横向节点 + 连接线动画
 * - 保留垂直时间线的输入→产出 / discovered / 置信度详情
 * - 顶部进度条 + 当前阶段标识
 *
 * 每级展示：
 * - 编译器名称与图标
 * - 输入 → 产出
 * - "发现了什么"（discovered）
 * - 置信度（confidence 0-1）
 * - 实时状态（pending/running/completed）
 *
 * 使用 framer-motion 实现逐级展开动画，完成度实时更新。
 */
import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { FileText, Network, GitBranch, Users, Box, CheckCircle2, Loader2, Circle, ListTree, Workflow } from 'lucide-react'
import { Card, CardBody } from '@/components/ui/Card'
import { InlineTabs } from '@/components/ui/InlineTabs'
import { FlowChain, type FlowStep } from '@/components/ui/FlowChain'
import { formatDuration } from '@/utils/format'
import type { CompilerStage, CompilerStageName } from '@/types'

interface CompilerAnimationProps {
  /** 五级编译阶段数据 */
  stages: CompilerStage[]
  /** 整体进度 0-100 */
  progress?: number
  /** 是否正在编译 */
  isCompiling?: boolean
}

const STAGE_META: Record<CompilerStageName, {
  label: string
  shortLabel: string
  icon: typeof FileText
  input: string
  output: string
  color: string
}> = {
  information: {
    label: '信息编译器',
    shortLabel: '信息',
    icon: FileText,
    input: '原始文件 · 表格 · 聊天记录',
    output: '清洗后的结构化信息',
    color: 'text-brand-500',
  },
  knowledge: {
    label: '知识编译器',
    shortLabel: '知识',
    icon: Network,
    input: '结构化信息',
    output: '企业知识图谱 · 关系网络',
    color: 'text-info',
  },
  process: {
    label: '流程编译器',
    shortLabel: '流程',
    icon: GitBranch,
    input: '知识图谱',
    output: '业务流程引擎 · SOP（标准操作流程） · 审批流',
    color: 'text-warning',
  },
  capability: {
    label: '能力编译器',
    shortLabel: '能力',
    icon: Users,
    input: '流程 + 知识',
    output: '岗位能力矩阵',
    color: 'text-success',
  },
  runtime: {
    label: '运行时编译器',
    shortLabel: '运行时',
    icon: Box,
    input: '以上全部',
    output: 'Enterprise Runtime',
    color: 'text-brand-600',
  },
}

function StageStatusIcon({ status }: { status: CompilerStage['status'] }) {
  if (status === 'completed') {
    return <CheckCircle2 className="w-5 h-5 text-success" aria-hidden="true" />
  }
  if (status === 'running' || status === 'paused') {
    return <Loader2 className="w-5 h-5 text-warning animate-spin" aria-hidden="true" />
  }
  if (status === 'failed') {
    return <Circle className="w-5 h-5 text-error" aria-hidden="true" />
  }
  return <Circle className="w-5 h-5 text-text-tertiary" aria-hidden="true" />
}

/** CompilerStage.status → FlowStep.status 映射 */
function toFlowStatus(status: CompilerStage['status']): FlowStep['status'] {
  if (status === 'completed') return 'done'
  if (status === 'running' || status === 'paused') return 'in_progress'
  return 'pending'
}

export function CompilerAnimation({
  stages,
  progress = 0,
  isCompiling = false,
}: CompilerAnimationProps) {
  const [view, setView] = useState<'timeline' | 'flow'>('timeline')

  // 横向流程图步骤
  const flowSteps: FlowStep[] = stages.map((stage) => {
    const meta = STAGE_META[stage.name]
    return {
      id: stage.name,
      label: meta.shortLabel,
      icon: meta.icon,
      status: toFlowStatus(stage.status),
      hint:
        stage.confidence != null && stage.confidence > 0
          ? `置信度 ${Math.round(stage.confidence * 100)}%`
          : undefined,
    }
  })

  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
          <div>
            <h3 className="text-h4 text-text-primary">五级编译</h3>
            <p className="text-sm text-text-tertiary mt-1">
              逐级将原始数据升维为可运行的组织实体
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <div className="text-2xl font-bold text-brand-500">{Math.round(progress)}%</div>
              <div className="text-xs text-text-tertiary">{isCompiling ? '编译中...' : '已完成'}</div>
            </div>
            <InlineTabs
              variant="segment"
              tabs={[
                { key: 'timeline', label: '时间线', icon: ListTree },
                { key: 'flow', label: '流程图', icon: Workflow },
              ]}
              activeKey={view}
              onChange={(k) => setView(k as 'timeline' | 'flow')}
            />
          </div>
        </div>

        {/* 进度条 */}
        <div className="h-1.5 bg-elevated rounded-full overflow-hidden mb-6">
          <motion.div
            className="h-full bg-gradient-to-r from-brand-400 to-brand-600 rounded-full"
            initial={{ width: 0 }}
            animate={{ width: `${progress}%` }}
            transition={{ duration: 0.5 }}
          />
        </div>

        <AnimatePresence mode="wait">
          {view === 'flow' ? (
            <motion.div
              key="flow"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25 }}
            >
              {/* 横向流程图（对标 prototype 部署流水线） */}
              <div className="py-4">
                <FlowChain steps={flowSteps} size="md" />
              </div>

              {/* 各阶段产出概览（紧凑表格行，避免纵向过长） */}
              <div className="mt-6 grid gap-2.5">
                {stages.map((stage, idx) => {
                  const meta = STAGE_META[stage.name]
                  const Icon = meta.icon
                  return (
                    <motion.div
                      key={stage.name}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.3, delay: idx * 0.08 }}
                      className={`flex items-center gap-3 rounded-lg border p-3 transition-colors ${
                        stage.status === 'running'
                          ? 'border-warning/40 bg-warning/5'
                          : stage.status === 'completed'
                            ? 'border-success/30 bg-success/5'
                            : stage.status === 'failed'
                              ? 'border-error/30 bg-error/5'
                              : 'border-border-default bg-elevated/20'
                      }`}
                    >
                      <div className={`w-8 h-8 rounded-md flex items-center justify-center flex-shrink-0 ${
                        stage.status === 'completed' ? 'bg-success/10' : stage.status === 'running' ? 'bg-warning/10' : 'bg-elevated'
                      }`}>
                        <Icon className={`w-4 h-4 ${meta.color}`} aria-hidden="true" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-text-muted">第 {idx + 1} 级</span>
                          <span className="text-sm font-medium text-text-primary truncate">{meta.label}</span>
                        </div>
                        <div className="text-xs text-text-tertiary truncate mt-0.5">
                          {meta.input} <span className="mx-1">→</span> <span className="text-text-secondary">{meta.output}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {stage.confidence != null && stage.confidence > 0 && (
                          <span className="text-xs text-text-muted font-mono">{Math.round(stage.confidence * 100)}%</span>
                        )}
                        <StageStatusIcon status={stage.status} />
                      </div>
                    </motion.div>
                  )
                })}
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="timeline"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25 }}
            >
              {/* 五级编译器垂直时间线 */}
              <div className="space-y-1">
                {stages.map((stage, idx) => {
                  const meta = STAGE_META[stage.name]
                  const Icon = meta.icon
                  const isLast = idx === stages.length - 1
                  const isCompleted = stage.status === 'completed'
                  const isActive = stage.status === 'running'

                  return (
                    <div key={stage.name} className="relative">
                      <motion.div
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ duration: 0.4, delay: idx * 0.15 }}
                        className={`relative flex gap-4 pb-6 ${!isLast ? '' : 'pb-0'}`}
                      >
                        {/* 时间线节点 */}
                        <div className="flex flex-col items-center flex-shrink-0">
                          <div
                            className={`w-10 h-10 rounded-full flex items-center justify-center border-2 ${
                              isCompleted
                                ? 'bg-success/10 border-success'
                                : isActive
                                  ? 'bg-warning/10 border-warning'
                                  : 'bg-elevated border-border-default'
                            }`}
                          >
                            <Icon className={`w-5 h-5 ${meta.color}`} aria-hidden="true" />
                          </div>
                          {/* 连接线 */}
                          {!isLast && (
                            <div
                              className={`w-0.5 flex-1 mt-1 min-h-[2rem] ${
                                isCompleted ? 'bg-success/40' : 'bg-border-default'
                              }`}
                            />
                          )}
                        </div>

                        {/* 阶段内容 */}
                        <div className="flex-1 pb-2">
                          <div className="flex items-center gap-2 mb-1">
                            <h4 className="text-sm font-medium text-text-primary">{meta.label}</h4>
                            <span className="text-xs text-text-tertiary">第 {idx + 1} 级</span>
                            <StageStatusIcon status={stage.status} />
                          </div>

                          <div className="text-xs text-text-tertiary mb-2">
                            <span>{meta.input}</span>
                            <span className="mx-1.5">→</span>
                            <span className="text-text-secondary">{meta.output}</span>
                          </div>

                          <AnimatePresence>
                            {(isCompleted || isActive) && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="overflow-hidden"
                              >
                                <div className="rounded-md bg-elevated/50 p-3 mb-2">
                                  <p className="text-xs text-text-secondary leading-relaxed">
                                    <span className="font-medium text-text-primary">发现了什么：</span>
                                    {stage.discovered || '（暂无）'}
                                  </p>
                                </div>
                                <div className="flex items-center gap-3 text-xs">
                                  <span className="text-text-tertiary">置信度</span>
                                  <div className="flex-1 h-1.5 bg-elevated rounded-full overflow-hidden max-w-[200px]">
                                    <div
                                      className={`h-full rounded-full ${
                                        (stage.confidence ?? 0) >= 0.8
                                          ? 'bg-success'
                                          : (stage.confidence ?? 0) >= 0.6
                                            ? 'bg-warning'
                                            : 'bg-error'
                                      }`}
                                      style={{ width: `${(stage.confidence ?? 0) * 100}%` }}
                                    />
                                  </div>
                                  <span className="font-medium text-text-primary">
                                    {/* 未完成阶段无置信度：显示「—」而非误导性的 0% */}
                                    {stage.confidence == null
                                      ? '—'
                                      : `${Math.round(stage.confidence * 100)}%`}
                                  </span>
                                  {/* 该级实际耗时（UI v4 新增）：让「哪一级慢」一目了然 */}
                                  {stage.duration_ms != null && stage.duration_ms > 0 && (
                                    <span className="text-text-tertiary font-mono ml-auto">
                                      耗时 {formatDuration(stage.duration_ms)}
                                    </span>
                                  )}
                                </div>
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </div>
                      </motion.div>
                    </div>
                  )
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </CardBody>
    </Card>
  )
}

export default CompilerAnimation
