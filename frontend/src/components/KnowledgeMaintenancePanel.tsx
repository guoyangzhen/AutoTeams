import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { toast } from 'sonner'
import {
  RefreshCw,
  History,
  ChevronDown,
  ChevronRight,
  Loader2,
  RotateCcw,
  AlertTriangle,
  CheckCircle2,
  GitBranch,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import {
  triggerIncrementalUpdate,
  listAgentVersions,
  rollbackAgentVersion,
  rollbackAgentKnowledge,
  type AgentVersion,
  type IncrementalUpdateStats,
} from '@/api/agents'

/**
 * 知识库运维面板（死代码激活：A1 + A2 + A3）
 *
 * 激活的后端能力（API 客户端早已写好，但前端从未调用）：
 * - POST /agents/{id}/incremental-update          → 手动触发增量同步（A1）
 * - GET  /agents/{id}/versions                     → Agent 配置版本历史（A3）
 * - POST /agents/{id}/versions/{vid}/rollback      → 回滚 Agent 配置（A3）
 * - POST /agents/{id}/rollback-knowledge/{vid}     → 回滚知识库快照（A2）
 *
 * 演示叙事：知识库运维闭环——
 *   文件变更 → 立即增量同步 → 出问题可查历史版本 → 一键回滚（配置或知识库）
 */

interface KnowledgeMaintenancePanelProps {
  agentId: string
}

export default function KnowledgeMaintenancePanel({ agentId }: KnowledgeMaintenancePanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [rollingBack, setRollingBack] = useState<string | null>(null)
  const [versions, setVersions] = useState<AgentVersion[]>([])
  const [lastSyncStats, setLastSyncStats] = useState<IncrementalUpdateStats | null>(null)
  const confirmDialog = useConfirmDialog()

  // 手动触发增量更新
  const handleSync = async () => {
    try {
      setSyncing(true)
      const result = await triggerIncrementalUpdate(agentId)
      setLastSyncStats(result)
      const s = result.stats
      const total = s.added + s.updated + s.deleted
      if (total === 0) {
        toast.success('增量同步完成：无变更（文件夹与数据库一致）')
      } else {
        toast.success(
          `增量同步完成：+${s.added} 更新${s.updated} 删除${s.deleted} 未变${s.unchanged}`,
        )
      }
    } catch (err) {
      console.error('增量同步失败:', err)
      toast.error(err instanceof Error ? err.message : '增量同步失败')
    } finally {
      setSyncing(false)
    }
  }

  // 加载版本历史
  const loadVersions = async () => {
    try {
      setLoadingVersions(true)
      const list = await listAgentVersions(agentId)
      setVersions(list)
    } catch (err) {
      console.error('加载版本历史失败:', err)
      toast.error('加载版本历史失败')
    } finally {
      setLoadingVersions(false)
    }
  }

  // 切换展开
  const handleToggle = async () => {
    const next = !expanded
    setExpanded(next)
    if (next && versions.length === 0) {
      await loadVersions()
    }
  }

  // 回滚 Agent 配置
  const handleRollbackConfig = async (versionId: string, version: string) => {
    const ok = await confirmDialog.ask({
      title: '回滚配置',
      description: `确定回滚到配置版本 ${version}？当前配置将被覆盖。`,
      confirmText: '回滚',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    try {
      setRollingBack(versionId)
      await rollbackAgentVersion(agentId, versionId)
      toast.success(`已回滚到配置版本 ${version}`)
      await loadVersions()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '回滚失败')
    } finally {
      setRollingBack(null)
    }
  }

  // 回滚知识库快照
  const handleRollbackKnowledge = async (versionId: string, version: string) => {
    const ok = await confirmDialog.ask({
      title: '回滚知识库',
      description: `确定回滚知识库到版本 ${version}？此操作将恢复知识库快照，当前知识库内容会被覆盖。建议先确认当前知识库无未提交的重要变更。`,
      confirmText: '回滚',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    try {
      setRollingBack(versionId)
      await rollbackAgentKnowledge(agentId, versionId)
      toast.success(`已回滚知识库到版本 ${version}`)
      await loadVersions()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '知识库回滚失败')
    } finally {
      setRollingBack(null)
    }
  }

  return (
    <div className="bg-surface border border-border-default rounded-lg overflow-hidden">
      {/* 折叠头部（div 而非 button，避免内部 Button 造成 button-in-button 嵌套） */}
      <div
        role="button"
        tabIndex={0}
        onClick={handleToggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            handleToggle()
          }
        }}
        className="w-full flex items-center justify-between p-4 hover:bg-elevated transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-brand-500" />
          <span className="text-sm font-medium text-text-primary">知识库运维</span>
          <span className="text-xs text-text-tertiary">
            · 增量同步 · 版本历史 · 一键回滚
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* 快捷增量同步按钮（折叠态也可用） */}
          <Button
            size="sm"
            variant="ghost"
            onClick={(e) => {
              e.stopPropagation()
              handleSync()
            }}
            disabled={syncing}
          >
            {syncing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            立即增量同步
          </Button>
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-text-tertiary" />
          ) : (
            <ChevronRight className="w-4 h-4 text-text-tertiary" />
          )}
        </div>
      </div>

      {/* 展开内容 */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="border-t border-border-subtle overflow-hidden"
          >
            <div className="p-4 space-y-4">
              {/* 上次同步结果 */}
              {lastSyncStats && (
                <div className="p-3 bg-elevated/50 rounded text-xs">
                  <div className="flex items-center gap-2 mb-2 text-text-secondary">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                    <span className="font-medium">上次增量同步结果</span>
                    <span className="text-text-tertiary font-mono">
                      {lastSyncStats.folder_path}
                    </span>
                  </div>
                  <div className="grid grid-cols-4 gap-2">
                    <div>
                      <span className="text-text-tertiary">新增 </span>
                      <span className="font-mono font-medium text-emerald-600">
                        {lastSyncStats.stats.added}
                      </span>
                    </div>
                    <div>
                      <span className="text-text-tertiary">更新 </span>
                      <span className="font-mono font-medium text-blue-600">
                        {lastSyncStats.stats.updated}
                      </span>
                    </div>
                    <div>
                      <span className="text-text-tertiary">删除 </span>
                      <span className="font-mono font-medium text-red-600">
                        {lastSyncStats.stats.deleted}
                      </span>
                    </div>
                    <div>
                      <span className="text-text-tertiary">未变 </span>
                      <span className="font-mono font-medium text-text-tertiary">
                        {lastSyncStats.stats.unchanged}
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {/* 版本历史 */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <History className="w-4 h-4 text-text-tertiary" />
                    <h4 className="text-sm font-medium text-text-primary">版本历史</h4>
                    <span className="text-xs text-text-tertiary">
                      ({versions.length} 个版本)
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={loadVersions}
                    disabled={loadingVersions}
                  >
                    <RefreshCw
                      className={`w-3.5 h-3.5 ${loadingVersions ? 'animate-spin' : ''}`}
                    />
                    刷新
                  </Button>
                </div>

                {loadingVersions ? (
                  <div className="flex justify-center py-6">
                    <Loader2 className="w-5 h-5 animate-spin text-brand-500" />
                  </div>
                ) : versions.length === 0 ? (
                  <p className="text-xs text-text-tertiary text-center py-6">
                    暂无版本历史。修改 Agent 配置（名称/描述/System Prompt/知识库参数）后会自动创建版本快照。
                  </p>
                ) : (
                  <div className="space-y-2 max-h-96 overflow-y-auto">
                    {versions.map((v) => (
                      <div
                        key={v.id}
                        className={`p-3 rounded border ${
                          v.is_active
                            ? 'bg-emerald-50/50 border-emerald-200'
                            : 'bg-elevated/30 border-border-subtle'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2 flex-wrap">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-mono text-sm font-medium text-text-primary">
                                v{v.version}
                              </span>
                              {v.is_active && (
                                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-emerald-100 text-emerald-700">
                                  <CheckCircle2 className="w-3 h-3" />
                                  当前
                                </span>
                              )}
                              <span className="text-xs text-text-tertiary">
                                {new Date(v.created_at).toLocaleString('zh-CN')}
                              </span>
                            </div>
                            {v.changelog && (
                              <p className="text-xs text-text-secondary mt-1 line-clamp-2">
                                {v.changelog}
                              </p>
                            )}
                          </div>

                          {/* 回滚按钮 */}
                          {!v.is_active && (
                            <div className="flex items-center gap-1 flex-shrink-0">
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleRollbackConfig(v.id, v.version)}
                                disabled={rollingBack === v.id}
                                title="回滚 Agent 配置（名称/描述/Prompt/参数）"
                              >
                                <RotateCcw className="w-3.5 h-3.5" />
                                回滚配置
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleRollbackKnowledge(v.id, v.version)}
                                disabled={rollingBack === v.id}
                                className="text-amber-700 hover:bg-amber-50"
                                title="回滚知识库到该版本快照"
                              >
                                <AlertTriangle className="w-3.5 h-3.5" />
                                回滚知识库
                              </Button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 说明 */}
              <div className="p-3 bg-amber-50/30 border border-amber-200/50 rounded text-xs text-text-tertiary leading-relaxed">
                <AlertTriangle className="w-3 h-3 inline mr-1 text-amber-600" />
                <span className="font-medium text-amber-700">运维说明：</span>
                「立即增量同步」基于 content_hash 比对，只处理新增/修改/删除的文件，速度远快于全量重建。
                「回滚配置」恢复 Agent 的 System Prompt 与参数；「回滚知识库」恢复该版本对应的知识库快照（向量库 + 文件元数据）。
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      {confirmDialog.dialog}
    </div>
  )
}
