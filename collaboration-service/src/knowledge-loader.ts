/**
 * 知识加载器 — 将 AI 员工的企业知识库物化到会话沙箱
 *
 * 职责：
 * - fetchAgentKnowledge(): 从后端拉取 Agent 的知识文档（GET /agents/{id}/knowledge）
 * - getSeedKnowledge(): 后端无知识时回退到内置企业知识种子（knowledge-seed/）
 * - materializeKnowledge(): 把知识文档写入沙箱 {cwd}/knowledge/，供技能检索
 * - writeAttachmentToKnowledge(): 用户上传的附件写入沙箱知识目录（纳入上下文）
 *
 * 这样协作工作台里每位 AI 员工都真正「连接企业知识库」，而非空沙箱。
 */
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'fs'
import { dirname, join, basename } from 'path'
import { fileURLToPath } from 'url'
import { listLocalPathTree, readLocalPathFile } from './runner-executor.js'
import { resolveWithinRootSync } from './path-guard.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

/** 后端 API 地址（与 auth.ts 保持一致） */
const backendUrl = (process.env.AUTOTEAMS_BACKEND_URL || process.env.AUTOFDE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

/** 沙箱内知识目录名 */
export const KNOWLEDGE_DIR_NAME = 'knowledge'

/** 内置企业知识种子目录（collaboration-service/knowledge-seed/） */
const SEED_DIR = join(__dirname, '..', 'knowledge-seed')

export interface KnowledgeFile {
  name: string
  content: string
}

/** 从后端拉取 Agent 知识文档；失败/为空时返回 [] */
export async function fetchAgentKnowledge(
  cookie: string | undefined,
  agentId: string,
): Promise<KnowledgeFile[]> {
  try {
    const response = await fetch(
      `${backendUrl}/api/v1/agents/${encodeURIComponent(agentId)}/knowledge`,
      { headers: cookie ? { Cookie: cookie } : {} },
    )
    if (!response.ok) return []
    const body = (await response.json()) as {
      data?: { files?: Array<{ name?: string; content?: string }> }
    }
    return (body.data?.files || [])
      .filter((f) => f && typeof f.name === 'string' && typeof f.content === 'string' && f.content.trim())
      .map((f) => ({ name: f.name as string, content: f.content as string }))
  } catch {
    return []
  }
}

/** 读取内置企业知识种子（后端无知识时的兜底保障） */
export function getSeedKnowledge(): KnowledgeFile[] {
  const files: KnowledgeFile[] = []
  if (!SEED_DIR) return files
  try {
    for (const name of readdirSync(SEED_DIR)) {
      const p = join(SEED_DIR, name)
      if (statSync(p).isFile()) {
        try {
          const content = readFileSync(p, 'utf8')
          if (content.trim()) files.push({ name, content })
        } catch {
          // 跳过无法读取的种子文件
        }
      }
    }
  } catch {
    // 种子目录不可用则返回空
  }
  return files
}

/** 把知识文档写入沙箱 {cwd}/knowledge/ 目录 */
export function materializeKnowledge(cwd: string, files: KnowledgeFile[]): number {
  if (!files || files.length === 0) return 0
  const dir = join(cwd, KNOWLEDGE_DIR_NAME)
  try {
    mkdirSync(dir, { recursive: true })
  } catch {
    return 0
  }
  let written = 0
  for (const f of files) {
    const safeName = sanitizeFileName(f.name)
    if (!safeName) continue
    try {
      // 名称已清洗，这里再走一次会话路径守卫（纵深防御：拒绝任何越出 cwd 的写入）。
      writeFileSync(resolveWithinRootSync(cwd, join(dir, safeName)), f.content, 'utf8')
      written++
    } catch {
      // 单个文件写入失败不影响其余
    }
  }
  return written
}

/** 把用户上传的附件写入沙箱知识目录（纳入上下文/知识来源） */
export function writeAttachmentToKnowledge(
  cwd: string,
  filename: string,
  data: Buffer,
): string | null {
  const safeName = sanitizeFileName(filename)
  if (!safeName) return null
  const dir = join(cwd, KNOWLEDGE_DIR_NAME)
  try {
    mkdirSync(dir, { recursive: true })
    const target = resolveWithinRootSync(cwd, join(dir, safeName))
    writeFileSync(target, data)
    return target
  } catch {
    return null
  }
}

/** 清洗文件名，防止路径遍历 */
function sanitizeFileName(name: string): string {
  const base = basename(name || '').replace(/[\/\\]/g, '_')
  if (!base || base === '.' || base === '..') return ''
  return base.replace(/[^\w.\-\u4e00-\u9fa5 ()（）]/g, '_').slice(0, 120)
}

// ============================================================
// §9.5 本地路径知识拉取（本地模式）
// ============================================================

/** 本地文件树条目（来自 Runner 的 listTree 结果，相对授权根目录） */
export interface LocalPathFileEntry {
  path: string
  type: 'file' | 'dir'
  size?: number
}

/** 从已授权本地路径拉取逻辑文件树（§9.5）。
 *
 * 本地模式下 AI 员工的操作对象就是用户授权目录，此函数用于
 * 把授权目录下的文件结构拉取到协作服务，供前端展示 / 注入知识摘要。
 * Runner 离线或失败时返回 []（不阻塞会话主流程）。
 */
export async function fetchLocalPathTree(grantId: string): Promise<LocalPathFileEntry[]> {
  try {
    const raw = await listLocalPathTree(grantId)
    return normalizeLocalTree(raw)
  } catch {
    return []
  }
}

/** 从已授权本地路径读取单个文件内容（§9.5）；失败返回 null。 */
export async function fetchLocalPathFile(grantId: string, path: string): Promise<string | null> {
  try {
    const raw = await readLocalPathFile(grantId, path)
    if (raw && typeof raw === 'object') {
      const data = raw as { content?: unknown }
      if (typeof data.content === 'string') return data.content
    }
    if (typeof raw === 'string') return raw
    return null
  } catch {
    return null
  }
}

/** 规范化 Runner 返回的 list 结果结构 */
function normalizeLocalTree(raw: unknown): LocalPathFileEntry[] {
  if (!raw || typeof raw !== 'object') return []
  const data = raw as { entries?: unknown }
  if (!Array.isArray(data.entries)) return []
  return data.entries.flatMap((e) => {
    if (!e || typeof e !== 'object') return []
    const entry = e as { path?: unknown; type?: unknown; size?: unknown }
    if (typeof entry.path !== 'string') return []
    const type = entry.type === 'dir' ? 'dir' : 'file'
    return [{ path: entry.path, type, size: typeof entry.size === 'number' ? entry.size : undefined }]
  })
}
