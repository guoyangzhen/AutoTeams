import { useNavigate } from 'react-router-dom'
import { Zap, Wrench, Code, type LucideIcon } from 'lucide-react'
import { Skill } from '@/types'
import { Card, CardBody, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

interface SkillCardProps {
  skill: Skill
}

// 根据技能类型选择图标
function getSkillIcon(skillType: string): LucideIcon {
  const type = skillType.toLowerCase()
  if (type.includes('code') || type.includes('api') || type.includes('script')) {
    return Code
  }
  if (type.includes('tool') || type.includes('wrench') || type.includes('helper')) {
    return Wrench
  }
  return Zap
}

export default function SkillCard({ skill }: SkillCardProps) {
  const navigate = useNavigate()
  const Icon = getSkillIcon(skill.skill_type)

  const handleClick = () => {
    navigate(`/skill/${skill.id}`)
  }

  return (
    <Card
      hover
      onClick={handleClick}
      className="animate-scale-in cursor-pointer group shadow-soft"
    >
      <CardBody>
        <div className="flex items-start justify-between mb-4">
          {/* 技能图标 — FIX-01: 36px/rounded-md，与 Home 内联卡片一致 */}
          <div className="w-9 h-9 rounded-md bg-brand-gradient-soft border border-brand-500/20 flex items-center justify-center transition-transform group-hover:scale-110">
            <Icon className="w-5 h-5 text-brand-400" />
          </div>
          {/* 类型徽章 */}
          <span className="inline-flex items-center px-2 py-1 rounded-full text-caption font-medium bg-brand-gradient-soft text-brand-400 border border-brand-500/20">
            {skill.skill_type}
          </span>
        </div>

        <h3 className="text-h3 text-text-primary mb-2 group-hover:text-brand-400 transition-colors">
          {skill.name}
        </h3>
        <p className="text-body text-text-tertiary line-clamp-2">
          {skill.description || '暂无描述'}
        </p>
      </CardBody>

      <CardFooter className="flex items-center justify-between">
        <span className="text-caption text-text-tertiary">
          创建于 {new Date(skill.created_at).toLocaleDateString('zh-CN')}
        </span>
        <Button variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); handleClick() }}>
          使用
        </Button>
      </CardFooter>
    </Card>
  )
}
