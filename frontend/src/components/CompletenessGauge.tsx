/**
 * CompletenessGauge — 完成度评估组件（五维雷达图版）。
 *
 * 对标 PRD §4.3 五维完成度评估框架：
 * 数据覆盖度(30%) / 流程覆盖度(25%) / 角色覆盖度(20%) / 置信度(15%) / 访谈完成度(10%)
 *
 * 升级要点（v2）：
 * - 圆环仪表 → RadarGauge 五维雷达图，直观展示各维度均衡度
 * - 左雷达 + 右维度详情列表（权重 / 得分 / 进度条），信息密度更高
 * - 中心总分 + 等级映射（≥80 可运行 / 60-79 基本可用 / <60 不完整）
 * - gaps 不再在此组件渲染，提升到页面层用 SectionGroup 统一管理，避免重复展示
 *
 * 纯 SVG 实现，无新图表库依赖。
 */
import { motion } from 'framer-motion'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { RadarGauge, type RadarDimension } from '@/components/ui/RadarGauge'
import type { CompletenessResult, CompletenessDimensions } from '@/types'

interface CompletenessGaugeProps {
  /** 完成度评估结果 */
  completeness: CompletenessResult | null
  /** 是否加载中 */
  loading?: boolean
}

/** 五维评估元数据（PRD §4.3 评估维度与权重） */
const DIMENSION_META: Array<{
  key: keyof CompletenessDimensions
  label: string
  shortLabel: string
  weight: number
  desc: string
}> = [
  { key: 'data_coverage', label: '数据覆盖度', shortLabel: '数据', weight: 0.30, desc: '已接入数字资产类别 / 预期类别' },
  { key: 'process_coverage', label: '流程覆盖度', shortLabel: '流程', weight: 0.25, desc: '已识别核心业务流程 / 预期流程' },
  { key: 'role_coverage', label: '角色覆盖度', shortLabel: '角色', weight: 0.20, desc: '已建模岗位 / 实际岗位，含能力矩阵' },
  { key: 'confidence', label: '置信度', shortLabel: '置信', weight: 0.15, desc: '高置信度实体占比（文档支撑 vs 推断）' },
  { key: 'interview_completion', label: '访谈完成度', shortLabel: '访谈', weight: 0.10, desc: '已回答关键访谈问题 / 触发总数' },
]

/** 总分 → 等级映射（PRD §4.3 结果映射） */
function levelOf(overall: number): { label: string; color: string; bg: string; desc: string } {
  if (overall >= 80) return { label: '可运行', color: 'text-success', bg: 'bg-success/10', desc: '建议部署' }
  if (overall >= 60) return { label: '基本可用', color: 'text-warning', bg: 'bg-warning/10', desc: '建议补充指定缺失项' }
  return { label: '不完整', color: 'text-error', bg: 'bg-error/10', desc: '需补充关键缺失项' }
}

/** 单维度得分 → 进度条颜色 */
function scoreBarClass(score: number): string {
  if (score >= 80) return 'bg-success'
  if (score >= 60) return 'bg-warning'
  return 'bg-error'
}

export function CompletenessGauge({ completeness, loading = false }: CompletenessGaugeProps) {
  // 后端 overall/dimensions 均为 0-1 分数；RadarGauge 的 overall 期望 0-100、
  // dimension.value 期望 0-1，展示层据此换算，避免出现 0.63%/1% 等错误单位。
  const overall = (completeness?.overall ?? 0) * 100
  const dimensions = completeness?.dimensions

  // 雷达图维度数据（后端已为 0-1，直接透传给 RadarGauge）
  const radarDimensions: RadarDimension[] = DIMENSION_META.map((meta) => ({
    id: meta.key,
    label: meta.shortLabel,
    value: dimensions?.[meta.key] ?? 0,
    weight: meta.weight,
  }))

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <h3 className="text-h4 text-text-primary">完成度评估</h3>
          <p className="text-sm text-text-tertiary mt-1">五级编译加权完成度</p>
        </CardHeader>
        <CardBody>
          <div className="flex flex-col md:flex-row gap-8 items-center">
            <div className="skeleton h-60 w-60 rounded-full" />
            <div className="flex-1 w-full space-y-4">
              {DIMENSION_META.map((m) => (
                <div key={m.key} className="skeleton h-10 rounded-md" />
              ))}
            </div>
          </div>
        </CardBody>
      </Card>
    )
  }

  const level = levelOf(overall)

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h3 className="text-h4 text-text-primary">完成度评估</h3>
            <p className="text-sm text-text-tertiary mt-1">
              五级编译加权完成度
            </p>
          </div>
          {completeness && (
            <span
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${level.bg} ${level.color}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${level.color.replace('text-', 'bg-')}`} />
              {level.label} · {level.desc}
            </span>
          )}
        </div>
      </CardHeader>
      <CardBody>
        <div className="flex flex-col lg:flex-row gap-8 items-center lg:items-start">
          {/* 左：五维雷达图 */}
          <motion.div
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5, ease: 'easeOut' }}
            className="flex-shrink-0"
          >
            <RadarGauge
              dimensions={radarDimensions}
              overall={overall}
              overallLabel={level.label}
              size={260}
            />
          </motion.div>

          {/* 右：维度详情列表 */}
          <div className="flex-1 w-full space-y-3">
            {DIMENSION_META.map((meta, idx) => {
              const score = (dimensions ? dimensions[meta.key] : 0) * 100
              return (
                <motion.div
                  key={meta.key}
                  initial={{ opacity: 0, x: 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.35, delay: 0.1 + idx * 0.06 }}
                  className="group"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-sm text-text-primary font-medium truncate">{meta.label}</span>
                      <span className="text-[10px] text-text-muted bg-elevated px-1.5 py-0.5 rounded flex-shrink-0">
                        权重 {Math.round(meta.weight * 100)}%
                      </span>
                    </div>
                    <span className={`text-sm font-semibold flex-shrink-0 ${score >= 80 ? 'text-success' : score >= 60 ? 'text-warning' : 'text-error'}`}>
                      {Math.round(score)}%
                    </span>
                  </div>
                  <div className="h-2 bg-elevated rounded-full overflow-hidden">
                    <motion.div
                      className={`h-full rounded-full ${scoreBarClass(score)}`}
                      initial={{ width: 0 }}
                      animate={{ width: `${score}%` }}
                      transition={{ duration: 0.6, delay: 0.15 + idx * 0.06, ease: 'easeOut' }}
                    />
                  </div>
                  <p className="text-[11px] text-text-muted mt-1 leading-relaxed opacity-0 group-hover:opacity-100 transition-opacity">
                    {meta.desc}
                  </p>
                </motion.div>
              )
            })}

            {/* 加权计算公式说明 */}
            <div className="pt-3 mt-2 border-t border-border-subtle">
              <p className="text-[11px] text-text-muted leading-relaxed font-mono">
                完成度 = 数据×30% + 流程×25% + 角色×20% + 置信×15% + 访谈×10%
              </p>
              <p className="text-[11px] text-text-muted mt-1 leading-relaxed">
                完成度衡量「企业运行模型建模到了什么程度」，用于引导补充数据，而非评判对错。
              </p>
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

export default CompletenessGauge
