import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend, LineChart, Line, XAxis, YAxis, CartesianGrid, BarChart, Bar } from 'recharts'
import { PieChart as PieChartIcon, TrendingUp, BarChart3 } from 'lucide-react'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'

interface KnowledgeStatsProps {
  agentId?: string
  // 可选数据，未传入时显示空状态（不再使用模拟数据）
  data?: {
    fileTypeDistribution: { name: string; value: number; color: string }[]
    knowledgeTimeline: { date: string; count: number }[]
    fileStatus: { name: string; value: number }[]
  }
}

// 图表 Tooltip 样式（使用 CSS 变量，自适应明暗主题）
const chartTooltip = {
  contentStyle: {
    backgroundColor: 'var(--bg-elevated)',
    border: '1px solid var(--border-default)',
    borderRadius: '8px',
    color: 'var(--text-primary)',
  },
}

// 图表坐标轴/网格色（使用 CSS 变量）
const chartAxis = { stroke: 'var(--text-tertiary)', fontSize: 12 }
const chartGrid = { stroke: 'var(--border-subtle)', strokeDasharray: '3 3' }

/** 空状态占位（无真实数据时展示，避免暴露模拟数据） */
function EmptyChartState({ height = 260 }: { height?: number }) {
  return (
    <div
      className="flex flex-col items-center justify-center text-center"
      style={{ height }}
    >
      <PieChartIcon className="h-8 w-8 text-text-muted mb-2" aria-hidden="true" />
      <p className="text-sm text-text-tertiary">暂无统计数据</p>
      <p className="text-xs text-text-muted mt-1">上传知识文件后将自动生成统计</p>
    </div>
  )
}

export function KnowledgeStats({ data }: KnowledgeStatsProps) {
  const hasData = !!data
  const fileTypeData = data?.fileTypeDistribution ?? []
  const timelineData = data?.knowledgeTimeline ?? []
  const statusData = data?.fileStatus ?? []

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
      {/* 图表 1：文件类型分布饼图 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <PieChartIcon className="h-4 w-4 text-brand-500" />
            <h3 className="text-h3 text-text-primary">文件类型分布</h3>
          </div>
        </CardHeader>
        <CardBody>
          {hasData && fileTypeData.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={fileTypeData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  innerRadius={40}
                  paddingAngle={2}
                >
                  {fileTypeData.map((entry, index) => (
                    <Cell key={index} fill={entry.color} stroke="none" />
                  ))}
                </Pie>
                <Tooltip {...chartTooltip} />
                <Legend wrapperStyle={{ color: 'var(--text-secondary)', fontSize: '12px' }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <EmptyChartState />
          )}
        </CardBody>
      </Card>

      {/* 图表 2：知识增长趋势线图 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-brand-500" />
            <h3 className="text-h3 text-text-primary">知识增长趋势</h3>
          </div>
        </CardHeader>
        <CardBody>
          {hasData && timelineData.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={timelineData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid {...chartGrid} />
                <XAxis dataKey="date" {...chartAxis} tickLine={false} axisLine={false} />
                <YAxis {...chartAxis} tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} />
                <Line
                  type="monotone"
                  dataKey="count"
                  stroke="#1E3A5F"
                  strokeWidth={2}
                  dot={{ fill: '#1E3A5F', r: 3 }}
                  activeDot={{ r: 5, fill: '#6B8CB1' }}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <EmptyChartState />
          )}
        </CardBody>
      </Card>

      {/* 图表 3：文件处理状态柱状图 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-brand-500" />
            <h3 className="text-h3 text-text-primary">文件处理状态</h3>
          </div>
        </CardHeader>
        <CardBody>
          {hasData && statusData.length > 0 ? (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={statusData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid {...chartGrid} />
                <XAxis dataKey="name" {...chartAxis} tickLine={false} axisLine={false} />
                <YAxis {...chartAxis} tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} cursor={{ fill: 'var(--brand-soft)' }} />
                <Bar dataKey="value" fill="#1E3A5F" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyChartState />
          )}
        </CardBody>
      </Card>
    </div>
  )
}

export default KnowledgeStats
