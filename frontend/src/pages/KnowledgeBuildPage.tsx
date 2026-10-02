/**
 * KnowledgeBuildPage — 选择构建方式（知识中枢引导页）。
 *
 * 提供三种构建企业知识与运行模型的方式，帮助用户按自身情况选择入口：
 * 1. 对话式访谈 → 交互式企业访谈（/interview）
 * 2. 通过文件夹 → 文件管理（/agents?tab=knowledge，文件夹上传）
 * 3. AI 自动生成 → 企业编译（/build?tab=compile）
 *
 * 该页为用户首选的构建入口，卡片化引导，避免用户面对空白知识库无从下手。
 */
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { MessageSquare, FolderUp, Sparkles, ArrowRight, CheckCircle2, Compass } from 'lucide-react'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardBody } from '@/components/ui/Card'

interface BuildOption {
  key: string
  icon: typeof MessageSquare
  title: string
  subtitle: string
  description: string
  points: string[]
  actionLabel: string
  to: string
  recommended?: boolean
  accent: string
  iconBg: string
}

const options: BuildOption[] = [
  {
    key: 'interview',
    icon: MessageSquare,
    title: '对话式访谈',
    subtitle: '顾问式渐进问答',
    description:
      '像企业顾问一样，通过系统化的渐进式提问，一步步完善企业画像、组织架构与运行模型。适合信息分散、希望边聊边构建的新企业。',
    points: [
      '7 大类业务域问题，覆盖销售/客服/采购/财务/人事等',
      '回答即时更新运行模型，完成度实时提升',
      '系统会随着认知完善，提出越来越精准的问题',
    ],
    actionLabel: '开始访谈',
    to: '/interview',
    recommended: true,
    accent: 'text-brand-500',
    iconBg: 'bg-brand-50 text-brand-500',
  },
  {
    key: 'folder',
    icon: FolderUp,
    title: '通过文件夹',
    subtitle: '文档批量导入',
    description:
      '上传企业文档或文件夹，系统自动解析、分块并索引为知识源，构建企业知识库。适合已有大量制度、手册、FAQ 等文档的企业。',
    points: [
      '支持单个文件与整个文件夹批量上传',
      '自动解析文档并向量化，支持精准检索',
      '按文件类型统计知识资产分布，实时可视化',
    ],
    actionLabel: '上传文件夹',
    to: '/agents?tab=knowledge',
    accent: 'text-blue-600',
    iconBg: 'bg-blue-50 text-blue-600',
  },
  {
    key: 'auto',
    icon: Sparkles,
    title: 'AI 自动生成',
    subtitle: '一键编译运行模型',
    description:
      '基于已导入的知识与访谈结果，AI 自动完成五级编译，生成可运行的企业 Runtime（组织、AI 员工、流程、协作关系）。适合快速启动验证。',
    points: [
      '五级编译：连接发现 → 分析解释 → 访谈 → 即时更新 → 编译输出',
      '自动生成 AI 员工团队与业务流程引擎',
      '编译产物可视化，支持版本管理与回滚',
    ],
    actionLabel: '开始编译',
    to: '/build?tab=compile',
    accent: 'text-emerald-600',
    iconBg: 'bg-emerald-50 text-emerald-600',
  },
]

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.1 } },
}
const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.45, ease: 'easeOut' as const } },
}

export default function KnowledgeBuildPage() {
  return (
    <Layout>
      <motion.div
        className="max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-8 space-y-8"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        <motion.div variants={itemVariants}>
          <PageHeader
            title="选择构建方式"
            subtitle="选择最合适的方式，构建企业的知识与运行模型"
          >
            <p className="text-sm text-text-muted mt-2 flex items-center gap-1.5">
              <Compass className="w-4 h-4" aria-hidden="true" />
              三种方式可组合使用，AI 自动生成将整合访谈与文档成果
            </p>
          </PageHeader>
        </motion.div>

        {/* 三种构建方式卡片 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {options.map((opt) => {
            const Icon = opt.icon
            return (
              <motion.div key={opt.key} variants={itemVariants} className="h-full">
                <Card className="h-full">
                  <CardBody className="flex flex-col h-full">
                    {/* 图标 + 推荐徽标 */}
                    <div className="flex items-start justify-between mb-4">
                      <span className={`w-12 h-12 rounded-xl flex items-center justify-center ${opt.iconBg}`}>
                        <Icon className="w-6 h-6" aria-hidden="true" />
                      </span>
                      {opt.recommended && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-brand-50 text-brand-600 border border-brand-200">
                          <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
                          推荐
                        </span>
                      )}
                    </div>

                    <h3 className="text-h4 text-text-primary">{opt.title}</h3>
                    <p className={`text-sm mt-0.5 ${opt.accent}`}>{opt.subtitle}</p>
                    <p className="text-sm text-text-tertiary mt-3 leading-relaxed">
                      {opt.description}
                    </p>

                    {/* 要点列表 */}
                    <ul className="mt-4 space-y-2 flex-1">
                      {opt.points.map((pt) => (
                        <li key={pt} className="flex items-start gap-2 text-sm text-text-secondary">
                          <span className={`w-1.5 h-1.5 rounded-full bg-brand-400 mt-1.5 flex-shrink-0`} />
                          <span>{pt}</span>
                        </li>
                      ))}
                    </ul>

                    {/* 操作按钮 */}
                    <div className="mt-6 pt-5 border-t border-border-subtle">
                      <Link
                        to={opt.to}
                        className={`group inline-flex items-center gap-2 text-sm font-medium ${opt.accent} hover:opacity-80 transition-opacity`}
                      >
                        {opt.actionLabel}
                        <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                      </Link>
                    </div>
                  </CardBody>
                </Card>
              </motion.div>
            )
          })}
        </div>

        {/* 底部组合提示 */}
        <motion.div variants={itemVariants}>
          <div className="rounded-xl border border-border-default bg-surface p-5 flex items-start gap-3">
            <Sparkles className="w-5 h-5 text-brand-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <div className="text-sm text-text-secondary leading-relaxed">
              <span className="font-medium text-text-primary">构建流程建议：</span>
              先通过「对话式访谈」补齐企业画像，或用「通过文件夹」导入已有文档；
              当信息基本完整后，运行「AI 自动生成」完成五级编译，即可在「企业构建」中查看可运行的
              Enterprise Runtime（组织架构、AI 员工、业务流程与协作关系）。
            </div>
          </div>
        </motion.div>
      </motion.div>
    </Layout>
  )
}