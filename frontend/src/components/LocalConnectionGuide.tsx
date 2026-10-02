/**
 * LocalConnectionGuide — 本地连接使用指南（协作工作台与本地工具桥接）
 *
 * 让用户用一两行命令即可把本机桥接到云端，使 AI 员工能在本地安全地读写文件。
 * 指南遵循原型核心风格（全中文、浅色、Apple 级细节）。
 *
 * 文案与实现对齐（避免宣称不存在的能力）：
 * - local-runner/src/executor.ts 只实现 list/read/write/delete，并显式拒绝 cli/agentic，
 *   因此本指南不宣称「调用本机终端工具 / Claude Code / OpenCode」。
 * - runner_session.claim_grant 在服务端「认领」连接时才消费一次性令牌：认领前的传输
 *   失败可用同一条命令重试，认领之后命令必然作废。local-runner/src/client.ts 认领后
 *   断线即退出，故不宣称「断线自动重连」，改为说明撤销 + 重新生成命令的恢复路径。
 * - 授权被认领后列表接口会给出 claimed，页面立即显示「连接中」并收回命令；但它早于
 *   connected 到达之前的窗口、以及未升级的服务端（不返回 claimed）里，「待连接」仍无法
 *   区分「命令尚未运行」与「已认领但尚未连上」，因此保留「撤销后重新添加」的兜底恢复路径。
 */
import { useState } from 'react'
import {
  Monitor, Terminal, Copy, Check, ChevronDown, ShieldCheck, FolderOpen, Sparkles,
} from 'lucide-react'

/** 当前部署源（浏览器地址栏的协议 + 域名），用于生成可复制的安装命令 */
const ORIGIN = typeof window !== 'undefined' ? window.location.origin : ''

const STEPS = [
  {
    icon: Monitor,
    title: '安装本地守护进程',
    desc: '在本机 PowerShell 中运行以下命令，一键安装自动化连接工具（仅需一次）',
    code: `irm ${ORIGIN || '<你的部署域名>'}/local-runner/install.ps1 | iex`,
  },
  {
    icon: FolderOpen,
    title: '授权本地文件夹',
    desc: '在右侧「本地连接」卡片中，输入要授权的本地文件夹路径并选择授权范围，点击「生成连接命令」',
    code: '（在网页中完成，无需命令）',
  },
  {
    icon: Terminal,
    title: '一次性连接',
    desc: '复制网页生成的连接命令，在本机 PowerShell 中运行一次；被服务端认领后该命令即作废',
    code: 'autoteams-runner connect --server wss://<你的部署域名>/bridge --token <一次性令牌> --grant <授权ID> --path "D:\\项目" --scope read_write',
  },
]

/** 命令行展示块（含复制） */
function CommandBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopyError('')
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // 复制失败必须可见：否则用户会误以为已复制成功。
      setCopyError('复制失败，请手动选中命令文本复制。')
      setCopied(false)
    }
  }
  return (
    <div className="space-y-1">
      <div className="flex items-start gap-2 bg-slate-50 border border-border-default rounded-md p-2.5">
        <code className="flex-1 text-[11px] leading-relaxed text-text-primary font-mono break-all select-all">
          {code}
        </code>
        <button
          onClick={handleCopy}
          className="inline-flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700 flex-shrink-0"
        >
          {copied ? (
            <Check className="w-3 h-3 text-emerald-500" aria-hidden="true" />
          ) : (
            <Copy className="w-3 h-3" aria-hidden="true" />
          )}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      {copyError && (
        <p className="text-[11px] text-red-500" role="alert">
          {copyError}
        </p>
      )}
    </div>
  )
}

export default function LocalConnectionGuide() {
  const [open, setOpen] = useState(false)

  return (
    <div id="local-connection-guide" className="border border-border-default rounded-lg bg-surface shadow-soft overflow-hidden">
      {/* 折叠头 */}
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-elevated transition-colors"
      >
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-brand-500" aria-hidden="true" />
          <span className="text-sm font-semibold text-text-primary">本地连接 · 使用指南</span>
          <span className="text-xs text-text-muted">让 AI 在本机直接读写你的文件</span>
        </div>
        <ChevronDown
          className={`w-4 h-4 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3">
          {/* 简要说明 */}
          <div className="flex items-start gap-2 bg-brand-50/60 border border-brand-100 rounded-md p-2.5">
            <ShieldCheck className="w-4 h-4 text-brand-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <p className="text-xs text-text-secondary leading-relaxed">
              通过「本地连接」，AI 员工可以在你授权的本地文件夹内安全地读取、修改、删除文件，
              全部操作被限制在该目录内。本地守护进程主动外连云端，路径与权限受严格校验；
              它不执行终端命令，也不会调用本机安装的 Claude Code、OpenCode 等 CLI 工具。
            </p>
          </div>

          {/* 一次性命令、断线恢复与待连接歧义说明 */}
          <div className="text-xs text-text-secondary leading-relaxed space-y-1">
            <p>
              连接命令里的令牌是<b className="font-medium text-text-primary">一次性</b>的：
              它由服务端在<b className="font-medium text-text-primary">认领连接的那一刻</b>消费作废。
              认领之前如果网络传输失败，重跑同一条命令即可；一旦被认领，命令就无法重跑，
              令牌过期后同样需要重新生成。
            </p>
            <p>
              命令被认领后，授权卡片会变成
              <b className="font-medium text-text-primary">连接中</b>，一次性命令同时被收回：
              这表示本机已接收该令牌、正在建立连接，稍等即可。
              若长时间停在「连接中」或「待连接」（认领信息尚未刷新到时无法区分两种情况），
              请在「本地连接」卡片中
              <b className="font-medium text-text-primary">撤销</b>该授权，再
              <b className="font-medium text-text-primary">添加</b>一次生成新命令。
            </p>
            <p>
              连接断开后守护进程会退出，不会自动重连，旧命令也无法重用；同样需要撤销后重新添加。
            </p>
          </div>

          {/* 三步指南 */}
          <div className="space-y-3">
            {STEPS.map((step, idx) => (
              <div key={step.title} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <div className="w-7 h-7 rounded-full bg-brand-500 text-white flex items-center justify-center text-xs font-medium flex-shrink-0">
                    {idx + 1}
                  </div>
                  {idx < STEPS.length - 1 && (
                    <div className="w-px flex-1 bg-border-default my-1" aria-hidden="true" />
                  )}
                </div>
                <div className="flex-1 space-y-1.5 pb-1">
                  <div className="flex items-center gap-1.5">
                    <step.icon className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
                    <span className="text-sm font-medium text-text-primary">{step.title}</span>
                  </div>
                  <p className="text-xs text-text-muted">{step.desc}</p>
                  <CommandBlock code={step.code} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}