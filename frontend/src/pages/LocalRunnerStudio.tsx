/**
 * LocalRunnerStudio — AutoTeams 5.0 具身物理执行器工作室。
 * 遵循克制·编辑式美学：白底发丝线、无彩色药丸胶囊、高阶排版层次。
 */
import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import {
  Monitor,
  ShieldAlert,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  Terminal,
  RefreshCw,
  Cpu,
} from 'lucide-react'
import Layout from '@/components/Layout'
import {
  listPhysicalRunners,
  listPhysicalTasks,
  listTwoFactorChallenges,
  confirmTwoFactor,
  readPhysicalAudit,
  type PhysicalRunner,
  type PhysicalTask,
  type TwoFactorChallenge,
  type PhysicalAuditEntry,
} from '@/api/runnerV2'

export default function LocalRunnerStudio() {
  const [runners, setRunners] = useState<PhysicalRunner[]>([])
  const [tasks, setTasks] = useState<PhysicalTask[]>([])
  const [challenges, setChallenges] = useState<TwoFactorChallenge[]>([])
  const [audits, setAudits] = useState<PhysicalAuditEntry[]>([])
  const [selectedTask, setSelectedTask] = useState<PhysicalTask | null>(null)
  const [loading, setLoading] = useState(false)
  const [confirmingId, setConfirmingId] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [rRes, tRes, cRes, aRes] = await Promise.all([
        listPhysicalRunners().catch(() => []),
        listPhysicalTasks().catch(() => []),
        listTwoFactorChallenges().catch(() => []),
        readPhysicalAudit(30).catch(() => []),
      ])
      setRunners(rRes)
      setTasks(tRes)
      setChallenges(cRes)
      setAudits(aRes)
      if (tRes.length > 0 && !selectedTask) {
        setSelectedTask(tRes[0])
      }
    } catch {
      toast.error('加载物理执行器状态失败')
    } finally {
      setLoading(false)
    }
  }, [selectedTask])

  useEffect(() => {
    loadData()
    const timer = setInterval(loadData, 5000)
    return () => clearInterval(timer)
  }, [loadData])

  const handle2FA = async (challengeId: string, decision: 'approve' | 'reject') => {
    setConfirmingId(challengeId)
    try {
      await confirmTwoFactor(challengeId, decision)
      toast.success(decision === 'approve' ? '已核准放行物理操作' : '已阻断驳回高危操作')
      await loadData()
    } catch {
      toast.error('双因子确认操作失败')
    } finally {
      setConfirmingId(null)
    }
  }

  return (
    <Layout>
      <div className="mx-auto w-full max-w-[1280px] px-10 py-10">
        {/* 页眉 */}
        <header className="flex items-start justify-between border-b border-[#E4E4E1] pb-8">
          <div>
            <h1 className="text-[30px] font-bold leading-9 tracking-tight text-[#0B0B0B]">
              具身物理执行器 2.0
            </h1>
            <p className="mt-1.5 text-[13px] text-[#6B6B66]">
              端侧无头浏览器 · 桌面辅助功能树 · 物理双因子门禁
            </p>
          </div>
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={loadData}
              disabled={loading}
              className="inline-flex items-center gap-1.5 text-[13px] text-[#6B6B66] hover:text-[#0B0B0B] transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新状态
            </button>
            <div className="flex items-center gap-2 border border-[#E4E4E1] bg-white px-3 py-1.5 rounded-[6px]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#1F7A4D]" />
              <span className="text-[12px] font-medium text-[#0B0B0B]">
                在线节点：{runners.filter((r) => r.online).length} / {runners.length}
              </span>
            </div>
          </div>
        </header>

        {/* 高危操作双因子确认横幅（待办挑战） */}
        {challenges.filter((c) => c.state === 'pending_endpoint_confirmation').length > 0 && (
          <section className="mt-8 border border-[#B23A2F]/30 bg-[#FFF5F5] p-5 rounded-[8px]">
            <div className="flex items-center gap-3 mb-3">
              <ShieldAlert className="h-5 w-5 text-[#B23A2F]" />
              <h2 className="text-[15px] font-semibold text-[#B23A2F]">
                高危物理操作待二次核准（2FA 门禁）
              </h2>
            </div>
            <div className="space-y-3">
              {challenges
                .filter((c) => c.state === 'pending_endpoint_confirmation')
                .map((c) => (
                  <div
                    key={c.challenge_id}
                    className="flex items-center justify-between bg-white border border-[#E4E4E1] p-3.5 rounded-[6px]"
                  >
                    <div>
                      <p className="text-[13px] font-medium text-[#0B0B0B]">{c.reason}</p>
                      <p className="text-[11px] text-[#6B6B66] mt-0.5 font-mono">
                        任务 ID: {c.task_id} · 到期时间: {new Date(c.expires_at).toLocaleTimeString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handle2FA(c.challenge_id, 'reject')}
                        disabled={confirmingId === c.challenge_id}
                        className="px-3 py-1.5 text-[12px] font-medium text-[#B23A2F] border border-[#B23A2F]/30 rounded-[4px] hover:bg-[#B23A2F]/5"
                      >
                        否决阻断
                      </button>
                      <button
                        type="button"
                        onClick={() => handle2FA(c.challenge_id, 'approve')}
                        disabled={confirmingId === c.challenge_id}
                        className="px-3 py-1.5 text-[12px] font-medium text-white bg-[#1F4FD8] rounded-[4px] hover:opacity-90"
                      >
                        核准放行
                      </button>
                    </div>
                  </div>
                ))}
            </div>
          </section>
        )}

        {/* 主体三列：在线节点 / 任务队列 / 视窗实时监视与审计 */}
        <div className="mt-8 grid grid-cols-12 gap-8">
          {/* 左列：在线 Runner 探针列表 (3/12) */}
          <div className="col-span-12 lg:col-span-4 space-y-6">
            <section className="bg-white border border-[#E4E4E1] p-5 rounded-[8px]">
              <div className="flex items-center justify-between pb-3 border-b border-[#E4E4E1]">
                <h3 className="text-[15px] font-semibold text-[#0B0B0B] flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-[#1F4FD8]" />
                  端侧 Runner 探针
                </h3>
                <span className="text-[12px] text-[#6B6B66]">{runners.length} 台注册</span>
              </div>
              <div className="mt-4 space-y-3">
                {runners.length === 0 ? (
                  <div className="py-8 text-center">
                    <Cpu className="h-6 w-6 mx-auto mb-2 text-[#C9C8C2]" aria-hidden="true" />
                    <p className="text-[13px] text-[#6B6B66]">暂无端侧执行器接入</p>
                    <p className="text-[11px] text-[#A3A29C] mt-1 leading-relaxed">
                      在员工本机按「协作工作台 → 本地连接」的安装与连接命令部署 Runner 后，
                      探针会自动出现在此处；未出现时可点「刷新状态」重试。
                    </p>
                  </div>
                ) : (
                  runners.map((r) => (
                    <div
                      key={r.runner_id}
                      className="border border-[#E4E4E1] p-3 rounded-[6px] hover:border-[#1F4FD8]/40 transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[13px] font-medium text-[#0B0B0B]">
                          {r.runner_id}
                        </span>
                        <span className="flex items-center gap-1.5 text-[11px] text-[#6B6B66]">
                          <span className={`w-1.5 h-1.5 rounded-full ${r.online ? 'bg-[#1F7A4D]' : 'bg-[#B23A2F]'}`} />
                          {r.online ? '在线' : '离线'}
                        </span>
                      </div>
                      <p className="text-[11px] text-[#6B6B66] mt-1 font-mono">
                        平台: {r.platform} · 版本: {r.version}
                      </p>
                      <div className="mt-2 flex flex-wrap gap-1">
                        {r.scopes.map((s) => (
                          <span
                            key={s}
                            className="text-[10px] font-mono text-[#6B6B66] bg-[#F4F4F3] px-1.5 py-0.5 rounded-[2px]"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            {/* 物理操作审计留痕 */}
            <section className="bg-white border border-[#E4E4E1] p-5 rounded-[8px]">
              <h3 className="text-[15px] font-semibold text-[#0B0B0B] pb-3 border-b border-[#E4E4E1] flex items-center gap-2">
                <Terminal className="h-4 w-4 text-[#6B6B66]" />
                物理操作审计留痕
              </h3>
              <div className="mt-4 space-y-2.5 max-h-[300px] overflow-y-auto pr-1">
                {audits.length === 0 ? (
                  <div className="py-8 text-center">
                    <Terminal className="h-5 w-5 mx-auto mb-2 text-[#C9C8C2]" aria-hidden="true" />
                    <p className="text-[12px] text-[#6B6B66]">暂无物理操作日志</p>
                    <p className="text-[11px] text-[#A3A29C] mt-1">
                      Runner 的文件读写与脚本执行在护栏裁决后都会在此留痕。
                    </p>
                  </div>
                ) : (
                  audits.slice(0, 10).map((a) => (
                    <div
                      key={a.trace_id}
                      className="text-[12px] border-b border-[#E4E4E1] pb-2 font-mono flex items-center justify-between"
                    >
                      <div>
                        <span className="text-[#0B0B0B] font-medium">{a.event}</span>
                        <span className="text-[#6B6B66] ml-2">{a.detail}</span>
                      </div>
                      <span
                        className={`text-[10px] font-medium ${
                          a.decision === 'block'
                            ? 'text-[#B23A2F]'
                            : a.decision === 'require_2fa'
                            ? 'text-[#9A6212]'
                            : 'text-[#1F7A4D]'
                        }`}
                      >
                        {a.decision.toUpperCase()}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>

          {/* 右列：物理任务流与视窗关键帧监视 (8/12) */}
          <div className="col-span-12 lg:col-span-8 space-y-6">
            {/* 视窗监视卡 */}
            <section className="bg-white border border-[#E4E4E1] p-5 rounded-[8px]">
              <div className="flex items-center justify-between pb-3 border-b border-[#E4E4E1]">
                <h3 className="text-[15px] font-semibold text-[#0B0B0B] flex items-center gap-2">
                  <Monitor className="h-4 w-4 text-[#1F4FD8]" />
                  端侧操作视窗关键帧（实时模拟）
                </h3>
                <span className="text-[11px] font-mono text-[#6B6B66]">无损灰度降采样 · 5fps</span>
              </div>
              <div className="mt-4 h-[260px] bg-[#1A1B23] rounded-[6px] flex flex-col items-center justify-center text-center p-6 border border-[#2E3039]">
                <Monitor className="h-10 w-10 text-[#6B6B66] mb-3 opacity-60" />
                <p className="text-[13px] text-white font-medium">
                {selectedTask ? `监视任务: ${selectedTask.task_id}` : '视窗待命中'}
              </p>
              <p className="text-[11px] text-[#A3A29C] mt-1 max-w-md font-mono">
                {selectedTask
                  ? `护栏裁决：${selectedTask.verdict.rule} · ${selectedTask.verdict.reason}`
                  : '当有无头浏览器或桌面辅助功能任务下发时，此处实时渲染端侧关键帧变化。'}
                </p>
              </div>
            </section>

            {/* 任务队列 */}
            <section className="bg-white border border-[#E4E4E1] p-5 rounded-[8px]">
              <div className="flex items-center justify-between pb-3 border-b border-[#E4E4E1]">
                <h3 className="text-[15px] font-semibold text-[#0B0B0B] flex items-center gap-2">
                  <Play className="h-4 w-4 text-[#1F7A4D]" />
                  物理执行任务流
                </h3>
                <span className="text-[12px] text-[#6B6B66]">共 {tasks.length} 项</span>
              </div>
              <div className="mt-4 divide-y divide-[#E4E4E1]">
                {tasks.length === 0 ? (
                  <div className="py-8 text-center">
                    <Play className="h-6 w-6 mx-auto mb-2 text-[#C9C8C2]" aria-hidden="true" />
                    <p className="text-[13px] text-[#6B6B66]">暂无物理任务记录</p>
                    <p className="text-[11px] text-[#A3A29C] mt-1 leading-relaxed">
                      通过对话或流程将无头浏览器 / 桌面辅助任务下发给在线 Runner 后，
                      任务流与护栏裁决会在此实时展示。
                    </p>
                  </div>
                ) : (
                  tasks.map((t) => (
                    <div
                      key={t.task_id}
                      onClick={() => setSelectedTask(t)}
                      className={`py-3.5 flex items-center justify-between cursor-pointer hover:bg-[#FAFAF9] px-2 rounded-[4px] transition-colors ${
                        selectedTask?.task_id === t.task_id ? 'bg-[#FAFAF9] font-medium' : ''
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <span className="w-1.5 h-1.5 rounded-full bg-[#1F4FD8]" />
                        <div>
                          <p className="text-[13px] text-[#0B0B0B]">
                            {t.verdict.reason}
                          </p>
                          <p className="text-[11px] text-[#6B6B66] font-mono mt-0.5">
                            ID: {t.task_id} · 通道: {t.channel} · 节点: {t.runner_id || '未绑定'}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-4">
                        <span className="text-[12px] font-mono text-[#6B6B66]">
                          {t.state.toUpperCase()}
                        </span>
                        {t.state === 'completed' && (
                          <CheckCircle2 className="h-4 w-4 text-[#1F7A4D]" />
                        )}
                        {t.state === 'failed' && <XCircle className="h-4 w-4 text-[#B23A2F]" />}
                        {t.state === 'awaiting_2fa' && (
                          <Clock className="h-4 w-4 text-[#9A6212]" />
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </Layout>
  )
}
