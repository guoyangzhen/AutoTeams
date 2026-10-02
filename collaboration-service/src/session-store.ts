/**
 * 协作会话的本地耐久状态存储。
 *
 * pi.dev AgentSession 本身只能在进程内恢复，因此此存储持久化用户可见的会话元数据、
 * 消息和工作目录。服务重启后，历史可继续查看且状态明确为 interrupted；重新打开会话时
 * 客户端可基于该历史创建新会话，而不会假装仍可安全复用已丢失的模型运行时。
 */
import { copyFile, mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'

export interface PersistedSessionMeta {
  sessionId: string
  agentId: string
  agentName: string
  positionLabel: string
  createdAt: string
  lastActiveAt: string
  messageCount: number
  lastMessagePreview: string
  mode: 'sandbox' | 'local'
  grantId?: string | null
  localScope?: string | null
  recoveryStatus: 'active' | 'interrupted' | 'closed'
}

export interface PersistedSessionMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

interface PersistedSessionRecord {
  meta: PersistedSessionMeta
  ownerUserId: string
  enterpriseId: string | null
  cwd: string
  messages: PersistedSessionMessage[]
}

interface SessionStoreDocument {
  version: 1
  sessions: Record<string, PersistedSessionRecord>
}

const stateFile = process.env.COLLAB_STATE_FILE || './data/collaboration-sessions.json'
const storeLockDir = `${stateFile}.lock`
const STORE_LOCK_TIMEOUT_MS = 10_000
const STORE_LOCK_STALE_MS = 60_000
let writeChain: Promise<void> = Promise.resolve()

/** 状态文件可观测状态；损坏与不可读必须与「首次启动还没有文件」区分开。 */
export type PersistedStoreStatus = 'ok' | 'missing' | 'corrupt' | 'unreadable'

export class SessionStoreError extends Error {
  readonly stateFile: string

  constructor(message: string) {
    super(message)
    this.name = 'SessionStoreError'
    this.stateFile = stateFile
  }
}

/** 状态文件存在但内容无法解析：拒绝一切写入，绝不用空状态覆盖历史。 */
export class SessionStoreCorruptError extends SessionStoreError {
  readonly state = 'corrupt' as const
  /** 已保留的损坏文件副本；备份失败时为 null（原文件保持不变） */
  readonly backupPath: string | null

  constructor(reason: string, backupPath: string | null) {
    super(
      `协作会话状态文件已损坏（${reason}），已拒绝写入以免覆盖历史。` +
        `原文件保持不变${backupPath ? `，损坏副本：${backupPath}` : ''}。` +
        `请人工检查 ${stateFile}（或恢复备份）后重启服务。`,
    )
    this.name = 'SessionStoreCorruptError'
    this.backupPath = backupPath
  }
}

/** 状态文件无法 I/O（只读卷、无权限、父路径不是目录等）。 */
export class SessionStoreUnavailableError extends SessionStoreError {
  readonly state = 'unreadable' as const
  readonly errnoCode: string

  constructor(action: string, errnoCode: string) {
    super(`协作会话状态${action}失败（${errnoCode}：${storeIoErrorMessage(errnoCode)}）：${stateFile}`)
    this.name = 'SessionStoreUnavailableError'
    this.errnoCode = errnoCode
  }
}

export class SessionStoreLockTimeoutError extends SessionStoreError {
  constructor(waitedMs: number) {
    super(
      `协作会话状态锁等待超时（${waitedMs}ms）：${storeLockDir}。` +
        '另一个进程可能正在写状态；确认无残留锁目录后重试。',
    )
    this.name = 'SessionStoreLockTimeoutError'
  }
}

/** 把 errno 翻译成运维可读的原因；只读卷必须被明确区分出来。 */
export function storeIoErrorMessage(errnoCode: string): string {
  switch (errnoCode) {
    case 'EROFS':
      return '所在卷为只读，无法写入（检查容器/磁盘挂载是否 ro）'
    case 'EACCES':
    case 'EPERM':
      return '进程无访问权限（检查状态目录属主与容器用户）'
    case 'ENOTDIR':
      return '父路径不是目录（COLLAB_STATE_FILE 的目录部分被同名文件占用）'
    case 'EISDIR':
      return '路径是一个目录而不是文件'
    case 'ENOSPC':
      return '磁盘空间不足'
    default:
      return '文件系统 I/O 失败'
  }
}

type StoreRead =
  | { status: 'ok'; store: SessionStoreDocument }
  | { status: 'missing' }
  | { status: 'corrupt'; reason: string }
  | { status: 'unreadable'; reason: string; errnoCode: string }

async function readStore(): Promise<StoreRead> {
  let raw: string
  try {
    raw = await readFile(stateFile, 'utf8')
  } catch (error) {
    const errnoCode = (error as NodeJS.ErrnoException).code || 'UNKNOWN'
    // 只有「文件还不存在」才是首次启动的正常状态；权限/只读卷必须显式报错。
    if (errnoCode === 'ENOENT') return { status: 'missing' }
    return { status: 'unreadable', errnoCode, reason: storeIoErrorMessage(errnoCode) }
  }

  if (raw.trim() === '') return { status: 'corrupt', reason: '文件内容为空' }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    return { status: 'corrupt', reason: `JSON 解析失败：${(error as Error).message}` }
  }

  const document = parsed as Partial<SessionStoreDocument> | null
  if (
    !document ||
    document.version !== 1 ||
    typeof document.sessions !== 'object' ||
    document.sessions === null ||
    Array.isArray(document.sessions)
  ) {
    return { status: 'corrupt', reason: '结构不符合 {version:1, sessions:{...}}' }
  }
  return { status: 'ok', store: document as SessionStoreDocument }
}

let corruptBackupPath: string | null = null

/**
 * 任何可能覆盖历史之前，先把损坏文件复制一份（`<stateFile>.corrupt-<时间戳>`）。
 * 每个进程只备份一次，避免同一份坏文件被反复复制。
 */
async function backupCorruptState(reason: string): Promise<string | null> {
  if (corruptBackupPath) return corruptBackupPath
  const backup = `${stateFile}.corrupt-${new Date().toISOString().replace(/[:.]/g, '-')}`
  try {
    await copyFile(stateFile, backup)
    corruptBackupPath = backup
    console.error(`[协作状态] 状态文件损坏（${reason}），已保留副本: ${backup}`)
  } catch (error) {
    console.error(`[协作状态] 无法备份损坏的状态文件: ${(error as Error).message}（原文件保持不变）`)
  }
  return corruptBackupPath
}

async function corruptStoreError(reason: string): Promise<SessionStoreCorruptError> {
  return new SessionStoreCorruptError(reason, await backupCorruptState(reason))
}

/** 读路径：损坏/不可读时抛出可区分的错，绝不返回空状态冒充成功。 */
async function readStoreOrThrow(): Promise<SessionStoreDocument> {
  const read = await readStore()
  if (read.status === 'ok') return read.store
  if (read.status === 'missing') return { version: 1, sessions: {} }
  if (read.status === 'corrupt') throw await corruptStoreError(read.reason)
  throw new SessionStoreUnavailableError('读取', read.errnoCode)
}

/** 只读探测：供启动检查/健康检查区分「空」「损坏」「不可读」，不产生任何副作用。 */
export async function inspectStoreState(): Promise<{
  status: PersistedStoreStatus
  sessions: number
  stateFile: string
  reason?: string
}> {
  const read = await readStore()
  if (read.status === 'ok') {
    return { status: 'ok', sessions: Object.keys(read.store.sessions).length, stateFile }
  }
  if (read.status === 'missing') return { status: 'missing', sessions: 0, stateFile }
  if (read.status === 'corrupt') return { status: 'corrupt', sessions: 0, stateFile, reason: read.reason }
  return { status: 'unreadable', sessions: 0, stateFile, reason: read.reason }
}

/** 递归创建状态文件父目录；锁目录与状态文件同级，必须先建好（AUD-25）。 */
async function ensureStateDir(): Promise<void> {
  const dir = dirname(stateFile)
  try {
    await mkdir(dir, { recursive: true })
  } catch (error) {
    throw new SessionStoreUnavailableError(`目录创建（${dir}）`, (error as NodeJS.ErrnoException).code || 'UNKNOWN')
  }
}

async function withStoreLock<T>(operation: () => Promise<T>): Promise<T> {
  // 旧实现直接 mkdir(<stateFile>.lock)，全新部署时父目录尚不存在 → ENOENT 启动失败。
  await ensureStateDir()

  const deadline = Date.now() + STORE_LOCK_TIMEOUT_MS
  while (true) {
    try {
      // mkdir 在同一文件系统内是原子操作；用目录锁避免引入平台相关的 flock 依赖。
      await mkdir(storeLockDir)
      break
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      // EROFS/EACCES/ENOTDIR 属于环境问题，必须与「锁被别人持有」区分开。
      if (code !== 'EEXIST') throw new SessionStoreUnavailableError('锁目录创建', code || 'UNKNOWN')

      try {
        const lockInfo = await stat(storeLockDir)
        // 进程被强制终止会遗留锁目录。会话 JSON 写入极短，超过 60 秒可安全回收。
        if (Date.now() - lockInfo.mtimeMs > STORE_LOCK_STALE_MS) {
          await rm(storeLockDir, { recursive: true, force: true })
          continue
        }
      } catch (statError) {
        if ((statError as NodeJS.ErrnoException).code !== 'ENOENT') throw statError
        continue
      }

      if (Date.now() >= deadline) throw new SessionStoreLockTimeoutError(STORE_LOCK_TIMEOUT_MS)
      await new Promise<void>((resolve) => setTimeout(resolve, 50))
    }
  }

  try {
    return await operation()
  } finally {
    await rm(storeLockDir, { recursive: true, force: true }).catch(() => undefined)
  }
}

async function writeStoreAtomically(store: SessionStoreDocument): Promise<void> {
  await mkdir(dirname(stateFile), { recursive: true })
  // 每次写入使用唯一临时文件；固定 .tmp 会让两个进程的写入彼此覆盖。
  const temporary = `${stateFile}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(store), 'utf8')
    await rename(temporary, stateFile)
  } finally {
    // rename 成功后临时文件已不存在；失败时尽力清理，避免下次启动误判为状态文件。
    await rm(temporary, { force: true }).catch(() => undefined)
  }
}

/**
 * 将整个“读取 → 修改 → 原子写入”临界区串行化，而不只是串行化写文件。
 * 否则两个并发 persistSession 都会基于旧快照写入，后一个调用会静默丢失前一个会话。
 */
async function mutateStore<T>(mutator: (store: SessionStoreDocument) => T | Promise<T>): Promise<T> {
  let result: T
  const operation = writeChain
    .catch(() => undefined) // 单次 I/O 失败不能让后续状态写入永久失效。
    .then(async () => withStoreLock(async () => {
      const read = await readStore()
      if (read.status === 'corrupt') throw await corruptStoreError(read.reason)
      if (read.status === 'unreadable') throw new SessionStoreUnavailableError('读取', read.errnoCode)
      const store: SessionStoreDocument = read.status === 'missing' ? { version: 1, sessions: {} } : read.store
      result = await mutator(store)
      await writeStoreAtomically(store)
    }))
  writeChain = operation
  await operation
  return result!
}

async function waitForPendingWrites(): Promise<void> {
  await writeChain.catch(() => undefined)
}

export async function persistSession(
  meta: Omit<PersistedSessionMeta, 'recoveryStatus'> | PersistedSessionMeta,
  ownerUserId: string,
  enterpriseId: string | null,
  cwd: string,
  messages: PersistedSessionMessage[],
): Promise<void> {
  await mutateStore((store) => {
    store.sessions[meta.sessionId] = {
      meta: { ...meta, recoveryStatus: 'active' },
      ownerUserId,
      enterpriseId,
      cwd,
      messages: messages.slice(-200),
    }
  })
}

export async function markActiveSessionsInterrupted(): Promise<void> {
  await mutateStore((store) => {
    for (const session of Object.values(store.sessions)) {
      if (session.meta.recoveryStatus === 'active') {
        session.meta.recoveryStatus = 'interrupted'
      }
    }
  })
}

export async function getPersistedSession(sessionId: string, ownerUserId: string): Promise<PersistedSessionRecord | null> {
  await waitForPendingWrites()
  const store = await readStoreOrThrow()
  const session = store.sessions[sessionId]
  return session?.ownerUserId === ownerUserId ? session : null
}

export async function listPersistedSessions(ownerUserId: string): Promise<PersistedSessionMeta[]> {
  await waitForPendingWrites()
  const store = await readStoreOrThrow()
  return Object.values(store.sessions)
    .filter((session) => session.ownerUserId === ownerUserId && session.meta.recoveryStatus !== 'closed')
    .map((session) => session.meta)
    .sort((a, b) => b.lastActiveAt.localeCompare(a.lastActiveAt))
}

export async function closePersistedSession(sessionId: string, ownerUserId: string): Promise<boolean> {
  return mutateStore((store) => {
    const session = store.sessions[sessionId]
    if (!session || session.ownerUserId !== ownerUserId) return false
    session.meta.recoveryStatus = 'closed'
    session.meta.lastActiveAt = new Date().toISOString()
    return true
  })
}
