import { useState, useEffect, useCallback, Fragment } from 'react'
import { Download, Search, AlertCircle, X, ChevronLeft, ChevronRight, RefreshCw, ShieldCheck, ShieldAlert } from 'lucide-react'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { getAuditLogs, exportAuditLogs, verifyAuditChain, type AuditLog, type AuditLogQuery, type AuditChainVerifyResult } from '@/api/auditLogs'
import { useAuth } from '@/hooks/useAuth'
import { IdChip } from '@/components/ui/IdChip'
import { JsonView } from '@/components/ui/JsonView'

const PAGE_SIZE = 50

// 常见操作类型预设（仅作下拉提示，可输入其他值）
const ACTION_OPTIONS = [
  { value: '', label: '全部操作' },
  { value: 'login', label: '登录' },
  { value: 'logout', label: '登出' },
  { value: 'create', label: '创建' },
  { value: 'update', label: '更新' },
  { value: 'delete', label: '删除' },
  { value: 'rollback', label: '回滚' },
  { value: 'export', label: '导出' },
  { value: 'apply', label: '应用优化' },
  { value: 'upload', label: '上传' },
]

const RESOURCE_TYPE_OPTIONS = [
  { value: '', label: '全部资源' },
  { value: 'agent', label: '智能体' },
  { value: 'file', label: '文件' },
  { value: 'skill', label: '技能' },
  { value: 'conversation', label: '会话' },
  { value: 'audit_log', label: '审计日志' },
  { value: 'enterprise', label: '企业' },
  { value: 'user', label: '用户' },
]

function formatDateTime(iso: string): string {
  if (!iso) return '-'
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    })
  } catch {
    return iso
  }
}

function truncate(str: string | null, max: number): string {
  if (!str) return '-'
  return str.length > max ? str.slice(0, max) + '...' : str
}

export default function AuditLogs() {
  const { user } = useAuth()
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [verifyResult, setVerifyResult] = useState<AuditChainVerifyResult | null>(null)

  // 筛选状态
  const [action, setAction] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [keyword, setKeyword] = useState('')
  const [userId, setUserId] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')

  // 非 admin 用户拒绝（前端兜底，后端也会 403）
  const isAuthorized = user?.role === 'admin'

  const loadLogs = useCallback(async (currentOffset: number) => {
    setLoading(true)
    setError(null)
    try {
      const query: AuditLogQuery = {
        limit: PAGE_SIZE,
        offset: currentOffset,
      }
      if (action) query.action = action
      if (resourceType) query.resource_type = resourceType
      if (keyword) query.keyword = keyword
      if (userId) query.user_id = userId
      if (startDate) query.start_date = new Date(startDate).toISOString()
      if (endDate) query.end_date = new Date(endDate).toISOString()

      const result = await getAuditLogs(query)
      setLogs(result.logs)
      setTotal(result.total)
      setOffset(currentOffset)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载审计日志失败'
      setError(msg)
      setLogs([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [action, resourceType, keyword, userId, startDate, endDate])

  useEffect(() => {
    if (isAuthorized) {
      loadLogs(0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthorized])

  const handleSearch = () => {
    loadLogs(0)
  }

  const handleReset = () => {
    setAction('')
    setResourceType('')
    setKeyword('')
    setUserId('')
    setStartDate('')
    setEndDate('')
    // 重置后立即加载（用空筛选）
    setTimeout(() => loadLogs(0), 0)
  }

  const handleExport = async () => {
    setExporting(true)
    setError(null)
    try {
      const query: AuditLogQuery = {}
      if (action) query.action = action
      if (resourceType) query.resource_type = resourceType
      if (keyword) query.keyword = keyword
      if (userId) query.user_id = userId
      if (startDate) query.start_date = new Date(startDate).toISOString()
      if (endDate) query.end_date = new Date(endDate).toISOString()

      await exportAuditLogs(query)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '导出失败'
      setError(msg)
    } finally {
      setExporting(false)
    }
  }

  const handlePrev = () => {
    if (offset >= PAGE_SIZE) {
      loadLogs(offset - PAGE_SIZE)
    }
  }

  const handleNext = () => {
    if (offset + PAGE_SIZE < total) {
      loadLogs(offset + PAGE_SIZE)
    }
  }

  const handleVerify = async () => {
    setVerifying(true)
    setError(null)
    setVerifyResult(null)
    try {
      const result = await verifyAuditChain(0)
      setVerifyResult(result)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '审计链验证失败'
      setError(msg)
    } finally {
      setVerifying(false)
    }
  }

  const dismissVerifyResult = () => {
    setVerifyResult(null)
  }

  const current_page = Math.floor(offset / PAGE_SIZE) + 1
  const total_pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  // 权限拦截
  if (!isAuthorized) {
    return (
      <Layout>
        <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8">
          <div className="p-6 rounded-lg bg-error/10 border border-error/30 text-error flex items-center gap-3">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            <div>
              <p className="font-medium">权限不足</p>
              <p className="text-sm text-error/80 mt-1">审计日志仅管理员可查看。当前账号角色：{user?.role || '未知'}</p>
            </div>
          </div>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8">
        {/* 标题栏 */}
        <PageHeader
          title="审计日志"
          subtitle="企业操作审计记录，保留 180 天，支持按操作类型、资源、用户、时间筛选与 CSV 导出。"
          actions={
            <div className="flex items-center gap-2">
              <button
                onClick={handleVerify}
                disabled={verifying}
                className="inline-flex items-center gap-2 px-4 py-2 bg-elevated text-text-primary border border-border-default rounded-md text-sm font-medium hover:bg-border-subtle disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="5.3.7 HMAC 链式防篡改：逐条重新计算 signature 比对，检测篡改/删除/插入"
              >
                {verifying ? <RefreshCw className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
                链式验证
              </button>
              <button
                onClick={handleExport}
                disabled={exporting || total === 0}
                className="inline-flex items-center gap-2 px-4 py-2 bg-brand-500 text-white rounded-md text-sm font-medium hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {exporting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                导出 CSV
              </button>
            </div>
          }
        />

        {/* 链式验证结果 */}
        {verifyResult && (
          <div
            className={`p-4 rounded-lg border mb-4 flex items-start gap-3 ${
              verifyResult.valid
                ? 'bg-success/10 border-success/30 text-success'
                : 'bg-error/10 border-error/30 text-error'
            }`}
          >
            {verifyResult.valid ? (
              <ShieldCheck className="w-5 h-5 flex-shrink-0 mt-0.5" />
            ) : (
              <ShieldAlert className="w-5 h-5 flex-shrink-0 mt-0.5" />
            )}
            <div className="flex-1 min-w-0">
              <p className="font-medium">
                {verifyResult.valid ? '审计链完整性验证通过' : '审计链完整性验证失败'}
              </p>
              <p className="text-sm opacity-90 mt-1">
                已验证 <span className="font-medium">{verifyResult.checked}</span> 条日志 · {verifyResult.message}
              </p>
              {verifyResult.broken_at && (
                <p className="text-xs opacity-75 mt-1 font-mono">断裂位置：{verifyResult.broken_at}</p>
              )}
            </div>
            <button
              onClick={dismissVerifyResult}
              className="text-current opacity-60 hover:opacity-100 transition-opacity"
              aria-label="关闭"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* 筛选栏 */}
        <div className="bg-surface border border-border-default rounded-lg p-4 mb-4">
          <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-text-secondary mb-1">操作类型</label>
              <select
                value={action}
                onChange={(e) => setAction(e.target.value)}
                className="w-full px-3 py-2 bg-elevated border border-border-default rounded-md text-sm focus:outline-none focus:border-brand-500"
              >
                {ACTION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-text-secondary mb-1">资源类型</label>
              <select
                value={resourceType}
                onChange={(e) => setResourceType(e.target.value)}
                className="w-full px-3 py-2 bg-elevated border border-border-default rounded-md text-sm focus:outline-none focus:border-brand-500"
              >
                {RESOURCE_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-text-secondary mb-1">用户 ID</label>
              <input
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="可选"
                className="w-full px-3 py-2 bg-elevated border border-border-default rounded-md text-sm focus:outline-none focus:border-brand-500"
              />
            </div>
            <div>
              <label className="block text-xs text-text-secondary mb-1">关键词</label>
              <input
                type="text"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜索资源 ID/操作"
                className="w-full px-3 py-2 bg-elevated border border-border-default rounded-md text-sm focus:outline-none focus:border-brand-500"
              />
            </div>
            <div>
              <label className="block text-xs text-text-secondary mb-1">开始时间</label>
              <input
                type="datetime-local"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full px-3 py-2 bg-elevated border border-border-default rounded-md text-sm focus:outline-none focus:border-brand-500"
              />
            </div>
            <div>
              <label className="block text-xs text-text-secondary mb-1">结束时间</label>
              <input
                type="datetime-local"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full px-3 py-2 bg-elevated border border-border-default rounded-md text-sm focus:outline-none focus:border-brand-500"
              />
            </div>
            <div className="flex items-end gap-2">
              <button
                onClick={handleSearch}
                disabled={loading}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-brand-500 text-white rounded-md text-sm font-medium hover:bg-brand-600 disabled:opacity-50 transition-colors"
              >
                <Search className="w-4 h-4" />
                搜索
              </button>
              <button
                onClick={handleReset}
                disabled={loading}
                className="px-4 py-2 bg-elevated text-text-secondary border border-border-default rounded-md text-sm hover:bg-border-subtle disabled:opacity-50 transition-colors"
              >
                重置
              </button>
            </div>
          </div>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="p-3 rounded-lg bg-error/10 border border-error/30 text-error text-sm flex items-center justify-between mb-4">
            <span className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => { setError(null); loadLogs(offset) }}
                className="text-error/80 hover:text-error text-xs px-2 py-1 rounded border border-error/30 hover:bg-error/10 transition-colors"
                aria-label="重试"
              >
                重试
              </button>
              <button onClick={() => setError(null)} className="text-error/60 hover:text-error transition-colors" aria-label="关闭">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* 日志表格 */}
        <div className="bg-surface border border-border-default rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-elevated border-b border-border-default">
                <tr className="text-left text-text-secondary">
                  <th className="px-3 py-2.5 font-medium">时间</th>
                  <th className="px-3 py-2.5 font-medium">用户 ID</th>
                  <th className="px-3 py-2.5 font-medium">操作</th>
                  <th className="px-3 py-2.5 font-medium">资源类型</th>
                  <th className="px-3 py-2.5 font-medium">资源 ID</th>
                  <th className="px-3 py-2.5 font-medium">IP</th>
                  <th className="px-3 py-2.5 font-medium">User-Agent</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default">
                {loading && logs.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-3 py-12 text-center text-text-tertiary">
                      <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
                      加载中...
                    </td>
                  </tr>
                ) : logs.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-3 py-12 text-center text-text-tertiary">
                      暂无审计日志记录
                    </td>
                  </tr>
                ) : (
                  logs.map((log) => (
                    <Fragment key={log.id}>
                      <tr
                        className="hover:bg-elevated cursor-pointer transition-colors"
                        onClick={() => setExpandedRow(expandedRow === log.id ? null : log.id)}
                      >
                        <td className="px-3 py-2.5 text-text-secondary whitespace-nowrap">{formatDateTime(log.created_at)}</td>
                        <td className="px-3 py-2.5">
                          {log.user_id ? <IdChip id={log.user_id} maxLength={12} tone="info" /> : '-'}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className="inline-flex px-2 py-0.5 rounded text-xs font-medium bg-brand-50 text-brand-500">
                            {log.action}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-text-secondary">{log.resource_type || '-'}</td>
                        <td className="px-3 py-2.5">
                          {log.resource_id ? <IdChip id={log.resource_id} maxLength={16} /> : '-'}
                        </td>
                        <td className="px-3 py-2.5 text-text-secondary font-mono text-xs">{log.ip_address || '-'}</td>
                        <td className="px-3 py-2.5 text-text-tertiary text-xs">{truncate(log.user_agent, 30)}</td>
                      </tr>
                      {expandedRow === log.id && (
                        <tr key={`${log.id}-detail`} className="bg-elevated/50">
                          <td colSpan={7} className="px-6 py-3">
                            <div className="text-xs text-text-secondary mb-2">详情：</div>
                            <JsonView value={log.details} title="审计详情" defaultCollapsed={false} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* 分页 */}
          {total > 0 && (
            <div className="px-4 py-3 border-t border-border-default flex items-center justify-between text-sm text-text-secondary">
              <div>
                共 <span className="font-medium text-text-primary">{total}</span> 条 ·
                第 <span className="font-medium text-text-primary">{current_page}</span> / {total_pages} 页
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handlePrev}
                  disabled={offset === 0 || loading}
                  className="inline-flex items-center gap-1 px-3 py-1.5 bg-elevated border border-border-default rounded text-xs hover:bg-border-subtle disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  上一页
                </button>
                <button
                  onClick={handleNext}
                  disabled={offset + PAGE_SIZE >= total || loading}
                  className="inline-flex items-center gap-1 px-3 py-1.5 bg-elevated border border-border-default rounded text-xs hover:bg-border-subtle disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  下一页
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </Layout>
  )
}
