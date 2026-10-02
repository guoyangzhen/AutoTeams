/**
 * EnterpriseCognition — 企业认知视图（P1-1 + UI重构阶段C）。
 *
 * 真实对接后端 cognition API（spec.md §10.7 WT1 端点）：
 * - GET /cognition/profile/{enterprise_id}          企业画像
 * - GET /cognition/operating-model/{enterprise_id}  运行模型
 *
 * 展示企业画像（行业/规模/主营业务/组织概览/成熟度标签）与
 * 运行模型（组织/角色/流程/KPI/权限）。接口失败或返回空时展示空态，绝不造假数据。
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Building2,
  Briefcase,
  Users,
  Target,
  TrendingUp,
  Lock,
  GitBranch,
  FileText,
  AlertTriangle,
  Sparkles,
  Boxes,
  Network,
} from 'lucide-react'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import { EmptyState } from '@/components/ui/EmptyState'
import { getEnterpriseProfile, getOperatingModel } from '@/api/cognition'
import type {
  EnterpriseProfile,
  OperatingModel,
  RoleDefinition,
  ProcessDefinitionModel,
} from '@/types'

interface EnterpriseCognitionProps {
  enterpriseId: string
}

/* ------------------------------------------------------------------ */
/*  小的展示辅助组件                                                    */
/* ------------------------------------------------------------------ */

/** 信息行：标签 + 值 */
function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <span className="text-xs text-text-tertiary flex-shrink-0">{label}</span>
      <span className="text-sm text-text-primary text-right">{value || '—'}</span>
    </div>
  )
}

/** 标签 chips */
function TagChips({ tags }: { tags: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.length > 0 ? (
        tags.map((t) => (
          <span
            key={t}
            className="inline-flex items-center px-2 py-0.5 rounded-md bg-brand-50 text-brand-700 text-xs font-medium"
          >
            {t}
          </span>
        ))
      ) : (
        <span className="text-xs text-text-tertiary">暂无标签</span>
      )}
    </div>
  )
}

/** 字符串列表 */
function StringList({ items, empty }: { items: string[]; empty?: string }) {
  if (!items || items.length === 0) {
    return <span className="text-xs text-text-tertiary">{empty ?? '暂无'}</span>
  }
  return (
    <ul className="space-y-1">
      {items.map((item, idx) => (
        <li key={idx} className="text-sm text-text-primary flex items-start gap-1.5">
          <span className="w-1 h-1 rounded-full bg-brand-400 mt-2 flex-shrink-0" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * ID 友好化展示：多条原始 ID 以「可点击芯片」呈现。
 * - 芯片内截断显示，悬停 title 显示完整 ID
 * - 点击复制完整 ID
 * - 标题处标注去重后的数量，避免一长串原始字符串铺满
 */
function IdChips({
  icon,
  label,
  ids,
  tone,
  empty,
}: {
  icon: React.ReactNode
  label: string
  ids: string[]
  tone: 'info' | 'error'
  empty: string
}) {
  const toneCls = tone === 'info' ? 'bg-info/10 text-info' : 'bg-error/10 text-error'
  const copy = (id: string) => {
    navigator.clipboard?.writeText(id).catch(() => {})
  }
  return (
    <div>
      <div className="text-xs text-text-tertiary mb-1.5 flex items-center gap-1">
        {icon} {label}
        {ids.length > 0 && <span className="text-text-muted">· {ids.length}</span>}
      </div>
      {ids.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {ids.map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => copy(id)}
              title={`${id}（点击复制）`}
              className={`inline-flex items-center px-2 py-0.5 rounded-md ${toneCls} text-xs font-mono max-w-[180px] truncate hover:opacity-80 transition-opacity`}
            >
              {id.length > 24 ? `${id.slice(0, 24)}…` : id}
            </button>
          ))}
        </div>
      ) : (
        <span className="text-xs text-text-tertiary">{empty}</span>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  企业画像                                                            */
/* ------------------------------------------------------------------ */

function ProfileView({ profile }: { profile: EnterpriseProfile }) {
  const { basic, tags, org_summary, maturity, business, gaps, completeness_score } = profile.profile
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {/* 基本信息 */}
      <Card>
        <CardHeader className="flex items-center gap-2">
          <Building2 className="w-4 h-4 text-brand-500" />
          <h3 className="text-h4 text-text-primary">企业基本信息</h3>
        </CardHeader>
        <CardBody>
          <InfoRow label="企业名称" value={basic?.name} />
          <InfoRow label="所属行业" value={basic?.industry} />
          <InfoRow label="企业规模" value={basic?.scale} />
          <InfoRow label="营收规模" value={basic?.revenue} />
          <InfoRow label="所在地" value={basic?.location} />
          <InfoRow label="成立时间" value={basic?.founded} />
        </CardBody>
      </Card>

      {/* 组织概览 + 成熟度 */}
      <Card>
        <CardHeader className="flex items-center gap-2">
          <Users className="w-4 h-4 text-brand-500" />
          <h3 className="text-h4 text-text-primary">组织与成熟度</h3>
        </CardHeader>
        <CardBody className="space-y-4">
          <div>
            <div className="text-xs text-text-tertiary mb-2">组织概览</div>
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-elevated rounded-md p-3 text-center">
                <div className="text-h4 text-text-primary">{org_summary?.department_count ?? '—'}</div>
                <div className="text-xs text-text-tertiary mt-0.5">部门</div>
              </div>
              <div className="bg-elevated rounded-md p-3 text-center">
                <div className="text-h4 text-text-primary">{org_summary?.headcount ?? '—'}</div>
                <div className="text-xs text-text-tertiary mt-0.5">员工数</div>
              </div>
              <div className="bg-elevated rounded-md p-3 text-center">
                <div className="text-h4 text-text-primary">{maturity?.level ?? '—'}</div>
                <div className="text-xs text-text-tertiary mt-0.5">AI 成熟度</div>
              </div>
            </div>
            {org_summary?.key_roles && org_summary.key_roles.length > 0 && (
              <div className="mt-3">
                <div className="text-xs text-text-tertiary mb-1.5">关键岗位</div>
                <TagChips tags={org_summary.key_roles} />
              </div>
            )}
          </div>
          <div className="border-t border-border-subtle pt-3">
            <div className="text-xs text-text-tertiary mb-2">AI 应用指标</div>
            <InfoRow
              label="自动化覆盖率"
              value={maturity ? `${Math.round(maturity.automation_coverage * 100)}%` : '—'}
            />
            <InfoRow label="AI 数字员工" value={maturity?.ai_workforce_count ?? '—'} />
            <InfoRow label="画像完整度" value={completeness_score ? `${Math.round(completeness_score * 100)}%` : '—'} />
          </div>
        </CardBody>
      </Card>

      {/* 业务概览 */}
      <Card>
        <CardHeader className="flex items-center gap-2">
          <Briefcase className="w-4 h-4 text-brand-500" />
          <h3 className="text-h4 text-text-primary">业务概览</h3>
        </CardHeader>
        <CardBody className="space-y-4">
          <div>
            <div className="text-xs text-text-tertiary mb-1.5">主营业务</div>
            <StringList items={business?.main_products ?? []} empty="暂无主营业务数据" />
          </div>
          <div>
            <div className="text-xs text-text-tertiary mb-1.5">目标行业</div>
            <StringList items={business?.target_industries ?? []} empty="暂无目标行业数据" />
          </div>
          <div>
            <div className="text-xs text-text-tertiary mb-1.5">核心流程</div>
            <StringList items={business?.core_processes ?? []} empty="暂无核心流程数据" />
          </div>
        </CardBody>
      </Card>

      {/* 标签 + 差距 */}
      <Card>
        <CardHeader className="flex items-center gap-2">
          <Target className="w-4 h-4 text-brand-500" />
          <h3 className="text-h4 text-text-primary">企业标签与差距</h3>
        </CardHeader>
        <CardBody className="space-y-4">
          <div>
            <div className="text-xs text-text-tertiary mb-1.5">企业标签</div>
            <TagChips tags={tags ?? []} />
          </div>
          <div>
            <div className="text-xs text-text-tertiary mb-1.5">认知差距</div>
            {gaps && gaps.length > 0 ? (
              <ul className="space-y-1.5">
                {gaps.map((g, idx) => (
                  <li key={idx} className="flex items-start gap-1.5 text-sm text-text-secondary">
                    <AlertTriangle className="w-3.5 h-3.5 text-warning mt-0.5 flex-shrink-0" />
                    <span>{g}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-xs text-text-tertiary">暂无差距记录</span>
            )}
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  运行模型                                                            */
/* ------------------------------------------------------------------ */

/** 角色卡 */
function RoleCard({ role }: { role: RoleDefinition }) {
  return (
    <div className="bg-elevated rounded-md p-3 border border-border-subtle">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium text-text-primary truncate">{role.title}</div>
        <span className="text-xs text-text-tertiary flex-shrink-0">{role.level}</span>
      </div>
      <div className="text-xs text-text-tertiary mt-0.5">{role.department}</div>
      {role.responsibilities && role.responsibilities.length > 0 && (
        <div className="mt-2">
          <div className="text-xs text-text-tertiary mb-1">职责</div>
          <StringList items={role.responsibilities} />
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {role.kpi_ids && role.kpi_ids.length > 0 && (
          <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-info/10 text-info">
            <TrendingUp className="w-3 h-3" /> KPI {role.kpi_ids.length}
          </span>
        )}
        {role.permission_ids && role.permission_ids.length > 0 && (
          <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-error/10 text-error">
            <Lock className="w-3 h-3" /> 权限 {role.permission_ids.length}
          </span>
        )}
      </div>
    </div>
  )
}

/** 流程卡 */
function ProcessCard({
  process,
  roleNameOf,
}: {
  process: ProcessDefinitionModel
  roleNameOf: (roleId: string | null | undefined) => string | null
}) {
  return (
    <div className="bg-elevated rounded-md p-3 border border-border-subtle">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium text-text-primary truncate">{process.name}</div>
        <span className="text-xs text-text-tertiary flex-shrink-0">{process.type}</span>
      </div>
      <div className="mt-2 text-xs text-text-tertiary space-y-0.5">
        <div>步骤数：{process.steps?.length ?? 0}</div>
        {process.trigger_event && <div>触发事件：{process.trigger_event}</div>}
        {process.owner_role_id && <div>负责人角色：{roleNameOf(process.owner_role_id)}</div>}
      </div>
    </div>
  )
}

function OperatingModelView({ model }: { model: OperatingModel }) {
  const { roles, processes, capabilities, gaps, organization, completeness } = model.model

  // 聚合所有角色的 KPI / 权限 ID 作为「KPI 与权限」视图
  const kpiIds = Array.from(new Set((roles ?? []).flatMap((r) => r.kpi_ids ?? [])))
  const permissionIds = Array.from(new Set((roles ?? []).flatMap((r) => r.permission_ids ?? [])))

  // role_id → 岗位名 映射：岗位能力/流程负责人等处的原始 ID 换成可读名称
  const roleNameOf = useCallback(
    (roleId: string | null | undefined): string | null => {
      if (!roleId) return null
      const role = (roles ?? []).find((r) => r.id === roleId)
      return role?.title || roleId
    },
    [roles],
  )

  return (
    <div className="space-y-5">
      {/* 概览条 */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-text-tertiary">
        <span className="inline-flex items-center gap-1.5">
          <Boxes className="w-3.5 h-3.5 text-brand-500" /> 模型版本 {model.version || model.model.version || '—'}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-brand-500" /> 完整度{' '}
          {completeness != null ? `${Math.round(completeness * 100)}%` : '—'}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 组织 */}
        <Card>
          <CardHeader className="flex items-center gap-2">
            <Network className="w-4 h-4 text-brand-500" />
            <h3 className="text-h4 text-text-primary">组织</h3>
          </CardHeader>
          <CardBody>
            {organization && Object.keys(organization).length > 0 ? (
              <div className="space-y-1.5">
                {Object.entries(organization).map(([key, value]) => (
                  <InfoRow
                    key={key}
                    label={key}
                    value={Array.isArray(value) ? value.join('、') : String(value)}
                  />
                ))}
              </div>
            ) : (
              <span className="text-xs text-text-tertiary">暂无组织数据</span>
            )}
          </CardBody>
        </Card>

        {/* KPI 与权限 */}
        <Card>
          <CardHeader className="flex items-center gap-2">
            <Lock className="w-4 h-4 text-brand-500" />
            <h3 className="text-h4 text-text-primary">KPI 与权限</h3>
          </CardHeader>
          <CardBody className="space-y-4">
            <IdChips
              icon={<TrendingUp className="w-3 h-3" />}
              label="KPI"
              ids={kpiIds}
              tone="info"
              empty="暂无 KPI 数据"
            />
            <IdChips
              icon={<Lock className="w-3 h-3" />}
              label="权限"
              ids={permissionIds}
              tone="error"
              empty="暂无权限数据"
            />
          </CardBody>
        </Card>
      </div>

      {/* 角色 */}
      <Card>
        <CardHeader className="flex items-center gap-2">
          <Users className="w-4 h-4 text-brand-500" />
          <h3 className="text-h4 text-text-primary">角色</h3>
          <span className="text-xs text-text-tertiary ml-auto">{roles?.length ?? 0} 个</span>
        </CardHeader>
        <CardBody>
          {roles && roles.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {roles.map((role) => (
                <RoleCard key={role.id} role={role} />
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-text-tertiary text-sm">暂无角色数据</div>
          )}
        </CardBody>
      </Card>

      {/* 流程 */}
      <Card>
        <CardHeader className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-brand-500" />
          <h3 className="text-h4 text-text-primary">业务流程</h3>
          <span className="text-xs text-text-tertiary ml-auto">{processes?.length ?? 0} 个</span>
        </CardHeader>
        <CardBody>
          {processes && processes.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {processes.map((process) => (
                <ProcessCard key={process.id} process={process} roleNameOf={roleNameOf} />
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-text-tertiary text-sm">暂无流程数据</div>
          )}
        </CardBody>
      </Card>

      {/* 能力 + 运行规则 + 差距 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="flex items-center gap-2">
            <FileText className="w-4 h-4 text-brand-500" />
            <h3 className="text-h4 text-text-primary">岗位能力</h3>
          </CardHeader>
          <CardBody>
            {capabilities && capabilities.length > 0 ? (
              <div className="space-y-3">
                {capabilities.map((cap) => (
                  <div key={cap.role_id} className="bg-elevated rounded-md p-3 border border-border-subtle">
                    <div className="text-sm font-medium text-text-primary">{roleNameOf(cap.role_id)}</div>
                    <div className="mt-1.5">
                      <div className="text-xs text-text-tertiary mb-1">所需能力</div>
                      <StringList items={cap.required_capabilities ?? []} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-text-tertiary text-sm">暂无能力数据</div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-brand-500" />
            <h3 className="text-h4 text-text-primary">运行差距</h3>
          </CardHeader>
          <CardBody>
            {gaps && gaps.length > 0 ? (
              <ul className="space-y-2">
                {gaps.map((gap, idx) => (
                  <li key={idx} className="bg-elevated rounded-md p-3 border border-border-subtle">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-text-primary">{gap.area}</span>
                      <span className="text-xs px-1.5 py-0.5 rounded bg-warning/10 text-warning flex-shrink-0">
                        {gap.severity}
                      </span>
                    </div>
                    <div className="text-xs text-text-tertiary mt-1">{gap.suggestion}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-center py-8 text-text-tertiary text-sm">暂无运行差距数据</div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  主组件：加载 + 空态 + 渲染                                          */
/* ------------------------------------------------------------------ */

export function EnterpriseCognition({ enterpriseId }: EnterpriseCognitionProps) {
  const [profile, setProfile] = useState<EnterpriseProfile | null>(null)
  const [model, setModel] = useState<OperatingModel | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setProfile(null)
    setModel(null)

    const load = async () => {
      if (!enterpriseId) {
        if (!cancelled) {
          setLoading(false)
          setError('未获取到企业 ID，无法加载企业认知数据')
        }
        return
      }
      const [profileRes, modelRes] = await Promise.allSettled([
        getEnterpriseProfile(enterpriseId),
        getOperatingModel(enterpriseId),
      ])
      if (cancelled) return
      if (profileRes.status === 'fulfilled') setProfile(profileRes.value)
      if (modelRes.status === 'fulfilled') setModel(modelRes.value)
      if (profileRes.status === 'rejected' && modelRes.status === 'rejected') {
        setError('企业认知数据加载失败，请稍后重试')
      }
      setLoading(false)
    }
    load()
    return () => {
      cancelled = true
    }
  }, [enterpriseId])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner className="text-brand-500" />
        <span className="ml-3 text-text-secondary text-sm">加载企业认知…</span>
      </div>
    )
  }

  // 无企业 ID 或接口全部失败 → 空态
  if (error || (!profile && !model)) {
    return (
      <EmptyState
        icon={Sparkles}
        title="暂无企业认知数据"
        description={
          error ??
          '尚未生成企业画像与运行模型。请先在「企业访谈」中完善企业信息，再回到这里查看认知洞察。'
        }
        variant="brand"
      />
    )
  }

  return (
    <div className="space-y-6">
      {profile && (
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Building2 className="w-4 h-4 text-brand-500" />
            <h2 className="font-serif-display text-base font-semibold text-text-primary">企业画像</h2>
          </div>
          <ProfileView profile={profile} />
        </section>
      )}
      {model && (
        <section>
          <div className="flex items-center gap-2 mb-3">
            <Boxes className="w-4 h-4 text-brand-500" />
            <h2 className="font-serif-display text-base font-semibold text-text-primary">运行模型</h2>
          </div>
          <OperatingModelView model={model} />
        </section>
      )}
    </div>
  )
}

export default EnterpriseCognition