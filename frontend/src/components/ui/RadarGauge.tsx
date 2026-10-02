/**
 * RadarGauge — 多维雷达图组件。
 *
 * 对标 PRD §4.3 五维完成度评估：
 * 数据覆盖度(30%) / 流程覆盖度(25%) / 角色覆盖度(20%) / 置信度(15%) / 访谈完成度(10%)
 *
 * 替换 CompletenessGauge 的圆环为五边形雷达图，
 * 直观展示各维度均衡度，支持点击轴高亮交互。
 *
 * 纯 SVG 实现，不引入新图表库，避免 bundle 膨胀。
 */
import { useState } from 'react'

export interface RadarDimension {
  id: string
  label: string
  /** 维度值 0-1（1 = 100%） */
  value: number
  /** 权重 0-1（用于标签显示） */
  weight: number
  /** 单位（默认 '%'） */
  unit?: string
}

interface RadarGaugeProps {
  dimensions: RadarDimension[]
  /** 总分 0-100（中心显示） */
  overall: number
  /** 总分评级文字（如"良好"） */
  overallLabel?: string
  /** SVG 尺寸（默认 240） */
  size?: number
  /** 高亮的维度 id（受控） */
  activeId?: string
  /** 维度点击回调 */
  onDimensionClick?: (id: string) => void
  className?: string
}

/** 计算正五边形第 i 个顶点坐标（从正上方开始，顺时针） */
function getPoint(center: number, radius: number, index: number, total: number) {
  const angle = -Math.PI / 2 + (index * 2 * Math.PI) / total
  return {
    x: center + radius * Math.cos(angle),
    y: center + radius * Math.sin(angle),
  }
}

/** 生成正多边形 points 字符串 */
function polygonPoints(center: number, radius: number, total: number): string {
  return Array.from({ length: total }, (_, i) => {
    const p = getPoint(center, radius, i, total)
    return `${p.x},${p.y}`
  }).join(' ')
}

/** 生成数据多边形 points 字符串（按各维度 value 缩放半径） */
function dataPolygonPoints(
  center: number,
  maxRadius: number,
  values: number[],
): string {
  return values
    .map((v, i) => {
      const r = maxRadius * Math.max(0, Math.min(1, v))
      const p = getPoint(center, r, i, values.length)
      return `${p.x},${p.y}`
    })
    .join(' ')
}

const SCORE_COLOR = (score: number) => {
  if (score >= 80) return '#16A34A' // success
  if (score >= 60) return '#D97706' // warning
  if (score >= 40) return '#2563EB' // info
  return '#DC2626' // error
}

export function RadarGauge({
  dimensions,
  overall,
  overallLabel,
  size = 240,
  activeId,
  onDimensionClick,
  className = '',
}: RadarGaugeProps) {
  const [internalActive, setInternalActive] = useState<string | undefined>(activeId)
  const active = activeId !== undefined ? activeId : internalActive

  const center = size / 2
  const maxRadius = size / 2 - 48 // 留出标签空间
  const gridLevels = [0.25, 0.5, 0.75, 1.0]
  const n = dimensions.length

  const handleDimClick = (id: string) => {
    if (activeId === undefined) {
      setInternalActive(id === active ? undefined : id)
    }
    onDimensionClick?.(id)
  }

  const scoreColor = SCORE_COLOR(overall)

  return (
    <div className={`flex flex-col items-center ${className}`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="多维完成度雷达图">
        {/* 网格：4 层正多边形 */}
        {gridLevels.map((level) => (
          <polygon
            key={level}
            points={polygonPoints(center, maxRadius * level, n)}
            fill="none"
            stroke="#E5E3DC"
            strokeWidth="1"
          />
        ))}

        {/* 轴线：从中心到各顶点 */}
        {dimensions.map((dim, i) => {
          const p = getPoint(center, maxRadius, i, n)
          return (
            <line
              key={dim.id}
              x1={center}
              y1={center}
              x2={p.x}
              y2={p.y}
              stroke={active === dim.id ? '#1E3A5F' : '#E5E3DC'}
              strokeWidth={active === dim.id ? '1.5' : '1'}
            />
          )
        })}

        {/* 数据多边形 */}
        <polygon
          points={dataPolygonPoints(
            center,
            maxRadius,
            dimensions.map((d) => d.value),
          )}
          fill="rgba(30, 58, 95, 0.12)"
          stroke="#1E3A5F"
          strokeWidth="2"
          strokeLinejoin="round"
        />

        {/* 数据点 */}
        {dimensions.map((dim, i) => {
          const r = maxRadius * Math.max(0, Math.min(1, dim.value))
          const p = getPoint(center, r, i, n)
          const isActive = active === dim.id
          return (
            <circle
              key={dim.id}
              cx={p.x}
              cy={p.y}
              r={isActive ? 5 : 3.5}
              fill={isActive ? '#1E3A5F' : '#FFFFFF'}
              stroke="#1E3A5F"
              strokeWidth="2"
              className={onDimensionClick ? 'cursor-pointer' : ''}
              onClick={() => handleDimClick(dim.id)}
            />
          )
        })}

        {/* 中心总分 */}
        <text
          x={center}
          y={center - 4}
          textAnchor="middle"
          fontSize="24"
          fontWeight="700"
          fill={scoreColor}
        >
          {Math.round(overall)}
        </text>
        <text
          x={center}
          y={center + 14}
          textAnchor="middle"
          fontSize="10"
          fill="#6B6B6B"
        >
          {overallLabel || '完成度'}
        </text>

        {/* 各轴标签 */}
        {dimensions.map((dim, i) => {
          const labelP = getPoint(center, maxRadius + 22, i, n)
          const valueP = getPoint(center, maxRadius + 36, i, n)
          const isActive = active === dim.id
          return (
            <g
              key={dim.id}
              className={onDimensionClick ? 'cursor-pointer' : ''}
              onClick={() => handleDimClick(dim.id)}
            >
              <text
                x={labelP.x}
                y={labelP.y}
                textAnchor="middle"
                fontSize="11"
                fontWeight={isActive ? '600' : '500'}
                fill={isActive ? '#1E3A5F' : '#525252'}
              >
                {dim.label}
              </text>
              <text
                x={valueP.x}
                y={valueP.y}
                textAnchor="middle"
                fontSize="10"
                fill="#8A8A8A"
              >
                {Math.round(dim.value * 100)}%
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

export default RadarGauge
