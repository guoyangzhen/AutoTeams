import type { IncomingMessage } from 'http'

export interface AuthenticatedUser {
  id: string
  role: string
  enterpriseId: string | null
}

export interface AuthorizedAgent {
  id: string
  name: string
  systemPrompt: string
}

const backendUrl = (process.env.AUTOTEAMS_BACKEND_URL || process.env.AUTOFDE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const allowedOrigins = new Set(
  (process.env.CORS_ALLOWED_ORIGINS || 'http://localhost:3000,http://127.0.0.1:3000')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean),
)

function parseCookies(header: string | undefined): Map<string, string> {
  const cookies = new Map<string, string>()
  for (const item of (header || '').split(';')) {
    const separator = item.indexOf('=')
    if (separator < 0) continue
    cookies.set(item.slice(0, separator).trim(), decodeURIComponent(item.slice(separator + 1).trim()))
  }
  return cookies
}

export function isAllowedOrigin(origin: string | undefined): boolean {
  return Boolean(origin && allowedOrigins.has(origin))
}

export function validateCsrf(request: IncomingMessage): boolean {
  const cookies = parseCookies(request.headers.cookie)
  const cookieToken = cookies.get('csrf_token')
  const headerToken = request.headers['x-csrf-token']
  return Boolean(cookieToken && typeof headerToken === 'string' && cookieToken === headerToken)
}

async function backendRequest(path: string, cookie: string | undefined): Promise<unknown> {
  const response = await fetch(`${backendUrl}/api/v1${path}`, {
    headers: cookie ? { Cookie: cookie } : {},
  })
  if (!response.ok) {
    throw new Error(response.status === 403 ? '无权访问该资源' : '认证已失效')
  }
  const body = await response.json() as { data?: unknown }
  return body.data
}

export async function authenticate(request: IncomingMessage): Promise<AuthenticatedUser> {
  const data = await backendRequest('/auth/me', request.headers.cookie) as {
    id: string
    role: string
    enterprise_id?: string | null
  }
  return {
    id: data.id,
    role: data.role,
    enterpriseId: data.enterprise_id ?? null,
  }
}

export async function authorizeAgent(request: IncomingMessage, agentId: string): Promise<AuthorizedAgent> {
  const data = await backendRequest(`/agents/${encodeURIComponent(agentId)}`, request.headers.cookie) as {
    id: string
    name: string
    system_prompt?: string | null
  }
  return {
    id: data.id,
    name: data.name,
    systemPrompt: data.system_prompt || `你是 ${data.name}，一名 AutoTeams AI 数字员工。`,
  }
}
