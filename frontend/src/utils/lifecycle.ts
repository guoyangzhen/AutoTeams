/**
 * 生命周期阶段统一标签和样式常量。
 *
 * 所有展示 AI 员工生命周期阶段的组件都应引用本文件，
 * 避免 WorkforceView / WorkforceGrid / AICompanyView 等页面间标签不一致。
 */
import type { LifecycleStage } from '@/types'

/** 生命周期阶段中文标签（统一完整形式） */
export const LIFECYCLE_LABELS: Record<LifecycleStage, string> = {
  recruit: '招聘中',
  training: '培训中',
  production: '生产中',
  evaluation: '绩效评估',
  continuous_learning: '持续学习',
  promotion: '晋升中',
  retired: '已退休',
}

/** 生命周期阶段标签样式（Tailwind 类名） */
export const LIFECYCLE_STYLES: Record<LifecycleStage, string> = {
  recruit: 'bg-elevated text-text-secondary',
  training: 'bg-warning/10 text-warning',
  production: 'bg-success/10 text-success',
  evaluation: 'bg-brand-50 text-brand-500',
  continuous_learning: 'bg-info/10 text-info',
  promotion: 'bg-success/10 text-success',
  retired: 'bg-elevated text-text-tertiary',
}
