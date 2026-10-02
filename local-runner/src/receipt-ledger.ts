/**
 * 端侧执行回执账本（AUD-18）。
 *
 * 审计结论：`physical-bridge.ts` 上报非 2xx 只记录日志，随后把任务从 `running`
 * 移除，没有任何"已完成"的持久记录。真实动作执行完成、但回执在断网/进程被杀
 * 的窗口里丢失时，下一次心跳会看到同样的 `pending_tasks` 并**再次执行**，
 * 对付款、提交、删除这类副作用就是重复动作。
 *
 * 本模块提供按 (task_id, step_id) 记账的本地账本：
 * - 动作**执行前**先写 `pending` 记录，执行后改写为 `done`/`failed`；
 * - 重连时把已 `done` 的记录重新上报（补回执），而不是重新执行；
 * - 仍处于 `pending`（上次进程中途退出，结果未知）默认**不重放**，
 *   交由云端裁决，避免重复副作用。
 *
 * 落盘为单文件 JSON，原子替换写入。达到容量上限时拒绝新动作，
 * 绝不淘汰旧记录后让同一个 step 再次执行。
 */
import { closeSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { dirname } from 'node:path'

export type ReceiptStatus = 'pending' | 'done' | 'failed'

/** 云端拒绝记账后的终态：动作确实执行过，但云端没有接受这条回执。 */
export type ReceiptRejection = {
  status: number
  reason: string
  at: string
}

export interface ReceiptRecord {
  task_id: string
  step_id: string
  status: ReceiptStatus
  ok: boolean
  data?: Record<string, unknown>
  error?: string
  started_at: string
  finished_at?: string
  reported: boolean
  rejection?: ReceiptRejection
}

export interface ReceiptLedger {
  /** 登记一次待执行动作；已有记录时返回 null，表示"这个 step 不要再执行"。 */
  begin(taskId: string, stepId: string): ReceiptRecord | null
  finish(record: ReceiptRecord): void
  markReported(taskId: string, stepId: string): void
  /** 云端拒绝该回执（404/409）：标记为 rejected，不当作已确认，且不无限重试。 */
  markRejected(taskId: string, stepId: string, status: number, reason: string): void
  /** 已完成但尚未被云端确认的记录（重连后补发）；rejected 记录不再自动重试。 */
  pendingReports(): ReceiptRecord[]
  /** 被云端拒绝、仍需人工处置的记录。 */
  rejected(): ReceiptRecord[]
  all(): ReceiptRecord[]
  size(): number
}

const MAX_RECORDS = 500

export class ReceiptLedgerFullError extends Error {
  constructor() {
    super('回执账本已满，物理动作已暂停；请先备份并核对历史回执')
  }
}

/** 同一设备的物理执行进程独占账本；残留锁需要人工确认旧进程已退出后处理。 */
export function acquireReceiptLedgerLease(path: string): () => void {
  mkdirSync(dirname(path), { recursive: true })
  const lockPath = `${path}.lock`
  const owner = randomUUID()
  let descriptor: number
  try {
    descriptor = openSync(lockPath, 'wx', 0o600)
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'EEXIST') {
      throw new Error('设备回执账本已被占用；请确认没有另一台本地 Runner 正在运行', { cause: err })
    }
    throw err
  }
  try {
    writeFileSync(descriptor, JSON.stringify({ owner, pid: process.pid, started_at: new Date().toISOString() }))
  } catch (err) {
    closeSync(descriptor)
    unlinkSync(lockPath)
    throw err
  }
  let released = false
  return () => {
    if (released) return
    released = true
    closeSync(descriptor)
    // 若有人手工移除并重建锁，不得删除后来进程的锁。
    try {
      const current = JSON.parse(readFileSync(lockPath, 'utf8')) as { owner?: string }
      if (current.owner === owner) unlinkSync(lockPath)
    } catch {
      // 文件被手工更改时保留现场；不再自行删除别人的锁。
    }
  }
}

function keyOf(taskId: string, stepId: string): string {
  return `${taskId}::${stepId}`
}

function buildLedger(records: Map<string, ReceiptRecord>, persist: () => void): ReceiptLedger {
  return {
    begin(taskId, stepId) {
      const key = keyOf(taskId, stepId)
      if (records.has(key)) return null
      if (records.size >= MAX_RECORDS) {
        throw new ReceiptLedgerFullError()
      }
      const record: ReceiptRecord = {
        task_id: taskId,
        step_id: stepId,
        status: 'pending',
        ok: false,
        started_at: new Date().toISOString(),
        reported: false,
      }
      records.set(key, record)
      persist()
      return record
    },

    finish(record) {
      records.set(keyOf(record.task_id, record.step_id), { ...record })
      persist()
    },

    markReported(taskId, stepId) {
      const existing = records.get(keyOf(taskId, stepId))
      if (!existing) return
      existing.reported = true
      persist()
    },

    markRejected(taskId, stepId, status, reason) {
      const existing = records.get(keyOf(taskId, stepId))
      if (!existing) return
      // 拒绝 ≠ 确认：保留 reported=false 与拒绝原因，交由人工处置，
      // 同时把它移出自动重发队列，避免每 10 秒刷一次必然失败的请求。
      existing.reported = false
      existing.rejection = { status, reason, at: new Date().toISOString() }
      persist()
    },

    pendingReports() {
      return [...records.values()]
        .filter(
          (record) => record.status !== 'pending' && !record.reported && !record.rejection,
        )
        .sort((a, b) => a.started_at.localeCompare(b.started_at))
    },

    rejected() {
      return [...records.values()]
        .filter((record) => record.rejection !== undefined)
        .sort((a, b) => a.started_at.localeCompare(b.started_at))
    },

    all() {
      return [...records.values()]
    },

    size() {
      return records.size
    },
  }
}

/** 内存账本仅供测试；进程退出后不保留去重记录。 */
export function inMemoryReceiptLedger(seed: ReceiptRecord[] = []): ReceiptLedger {
  const records = new Map<string, ReceiptRecord>()
  for (const record of seed) records.set(keyOf(record.task_id, record.step_id), { ...record })
  return buildLedger(records, () => undefined)
}

/** 打开（或创建）磁盘账本。已有文件损坏时拒绝物理执行并保留原文件。 */
export function fileReceiptLedger(path: string): ReceiptLedger {
  mkdirSync(dirname(path), { recursive: true })
  const records = new Map<string, ReceiptRecord>()

  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as LedgerDocument
    if (parsed?.version !== 1 || !parsed.records ||
      typeof parsed.records !== 'object' || Array.isArray(parsed.records)) {
      throw new Error('回执账本格式无效；请核对原文件，物理动作已暂停')
    }
    for (const [key, value] of Object.entries(parsed.records)) records.set(key, value)
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code
    if (code !== 'ENOENT') throw new Error('回执账本无法读取；原文件已保留，物理动作已暂停', { cause: err })
  }

  const persist = (): void => {
    const document: LedgerDocument = { version: 1, records: Object.fromEntries(records) }
    const temporary = `${path}.${process.pid}.tmp`
    writeFileSync(temporary, JSON.stringify(document), 'utf8')
    renameSync(temporary, path)
  }

  return buildLedger(records, persist)
}

interface LedgerDocument {
  version: 1
  records: Record<string, ReceiptRecord>
}
