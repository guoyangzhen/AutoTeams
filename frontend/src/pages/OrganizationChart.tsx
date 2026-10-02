/**
 * OrganizationChart — 组织架构图独立页面。
 *
 * 从 RuntimeVisualizer 抽出的独立入口（PRD §4.6 组织运行时），
 * 复用 OrganizationView（mermaid 树状布局，可缩放、随数据自动更新），让用户能从「AI 员工」hub
 * 直接进入查看企业部门层级与汇报关系，而非只能藏在运行时可视化里。
 *
 * 数据来源：runtimeApi.getRuntime(enterpriseId) → runtime.organization.departments
 */
import { useState, useEffect, useCallback } from 'react'
import { Building2 } from 'lucide-react'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { Spinner } from '@/components/ui/Spinner'
import { ApiErrorState } from '@/components/ApiErrorState'
import { OrganizationView } from '@/components/RuntimeVisualizer'
import * as runtimeApi from '@/api/runtime'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import type { RuntimeCompileResult } from '@/types'

export default function OrganizationChart() {
  const enterpriseId = useEnterpriseId()
  const [runtime, setRuntime] = useState<RuntimeCompileResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      const rt = await runtimeApi.getRuntime(enterpriseId)
      setRuntime(rt)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载组织架构失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  useEffect(() => {
    loadData()
  }, [loadData])

  if (loading && !runtime) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  const departments = runtime?.organization?.departments

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-8 space-y-8">
        <PageHeader title="组织架构" subtitle="企业部门层级与汇报关系" />

        {error && (
          <ApiErrorState message={error} onRetry={loadData} retrying={loading} />
        )}

        {departments && departments.length > 0 ? (
          <div className="rounded-lg border border-border-default bg-surface p-4">
            <OrganizationView departments={departments} agents={runtime?.agents} />
          </div>
        ) : (
          <div className="text-center py-16">
            <Building2 className="w-10 h-10 text-text-muted mx-auto mb-3" aria-hidden="true" />
            <p className="text-sm font-medium text-text-secondary">暂无组织架构数据</p>
            <p className="text-xs text-text-tertiary mt-1">请先完成企业编译</p>
          </div>
        )}
      </div>
    </Layout>
  )
}
