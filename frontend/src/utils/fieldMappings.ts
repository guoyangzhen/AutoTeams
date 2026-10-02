/**
 * 字段映射工具 — 将后端英文字段/ID 转为用户友好的中文显示。
 *
 * 解决问题：前端不应直接展示 pos_sales、agent-xxx-001、training 等原始字段。
 * 所有展示层统一使用此模块做转换，保证一致性。
 */

/** 岗位 ID → 中文名称 */
export const POSITION_LABELS: Record<string, string> = {
  pos_sales: '销售经理',
  pos_sales_manager: '销售经理',
  pos_sales_rep: '销售代表',
  pos_pre_sales: '售前工程师',
  pos_presales: '售前工程师',
  pos_presales_engineer: '售前工程师',
  pos_presales_tech: '售前技术支持',
  pos_finance: '财务专员',
  pos_finance_manager: '财务经理',
  pos_customer_service: '客服专员',
  pos_cs_specialist: '客服专员',
  pos_after_sales: '售后工程师',
  pos_aftersales: '售后工程师',
  pos_aftersales_engineer: '售后工程师',
  pos_aftersales_specialist: '售后服务专员',
  pos_hr: '人事专员',
  pos_procurement: '采购专员',
  pos_marketing: '市场专员',
  pos_logistics: '物流专员',
  pos_tech: '技术工程师',
  pos_technician: '技术员',
  pos_admin: '管理员',
}

/** role-xxx / role_xxx 标识 → 中文岗位名 */
export const ROLE_LABELS: Record<string, string> = {
  'role-sales-rep': '销售代表',
  'role-sales-manager': '销售经理',
  'role-presales': '售前工程师',
  'role-finance': '财务专员',
  'role-customer-service': '客服专员',
  'role-after-sales': '售后工程师',
}

/** 生命周期阶段 → 中文标签 */
export const LIFECYCLE_LABELS: Record<string, string> = {
  training: '培训中',
  production: '生产中',
  draft: '草稿',
  archived: '已归档',
}

/** 生命周期阶段 → 颜色类名 */
export const LIFECYCLE_COLORS: Record<string, string> = {
  training: 'text-warning bg-warning/10',
  production: 'text-success bg-success/10',
  draft: 'text-text-tertiary bg-elevated',
  archived: 'text-text-muted bg-elevated',
}

/** 协作事件类型 → 中文标签 */
export const EVENT_TYPE_LABELS: Record<string, string> = {
  inquiry_received: '收到询盘',
  product_query: '产品查询',
  quotation_generated: '报价生成',
  approval_submitted: '提交审批',
  approval_approved: '审批通过',
  approval_rejected: '审批拒绝',
  order_synced: '订单同步',
  after_sales: '售后处理',
  handoff: '任务转交',
  escalation: '问题升级',
  error: '错误',
}

/** 建议类型 → 中文标签 */
export const SUGGESTION_TYPE_LABELS: Record<string, string> = {
  knowledge: '知识补充',
  process: '流程优化',
  capability: '能力新增',
  organization: '组织调整',
}

/** diff section → 中文标签 */
export const SECTION_LABELS: Record<string, string> = {
  agents: 'AI 员工',
  process_engines: '流程引擎',
  organization: '组织架构',
  'organization.departments': '部门架构',
  collaboration_graph: '协作关系',
  'collaboration_graph.edges': '协作关系',
  knowledge_index: '知识索引',
  tool_registry: '工具注册表',
  completeness: '完成度',
  model_version: '模型版本',
}

/** 常见 KPI 标识 → 中文标签（用于 WorkforceView KPI 达成展示） */
const KPI_LABELS: Record<string, string> = {
  task_completion_rate: '任务完成率',
  response_time: '响应时效',
  customer_satisfaction: '客户满意度',
  quote_accuracy: '报价准确率',
  approval_pass_rate: '审批通过率',
  order_conversion: '订单转化率',
  knowledge_coverage: '知识覆盖率',
  tool_utilization: '工具使用率',
  error_rate: '差错率',
  sla_compliance: 'SLA 达成率',
  first_response_time: '首响时长',
  resolution_rate: '问题解决率',
}

/**
 * 将 KPI 标识（snake_case 英文）转为中文标签。
 * 命中映射表优先；否则做通用 snake_case → 中文兜底（避免直接暴露原始英文）。
 */
export function kpiToLabel(kpiId: string | undefined | null): string {
  if (!kpiId) return '指标'
  if (KPI_LABELS[kpiId]) return KPI_LABELS[kpiId]
  // 通用兜底：snake_case → 空格分隔，首字母大写后转小写中混排仍不友好，
  // 此处直接返回"业务指标"避免暴露原始 key
  return '业务指标'
}

/**
 * 将 diff 变更的 key（如 agent_id/step_name）转为友好展示。
 * 截断过长的 UUID 类标识。
 */
export function diffKeyToLabel(key: string | undefined | null): string {
  if (!key) return '—'
  if (isUuid(key)) return '记录项'
  // 若是 snake_case 英文 key，转为通用描述
  if (/^[a-z_]+$/.test(key)) {
    const map: Record<string, string> = {
      agent_id: '员工配置',
      step_name: '流程步骤',
      approver_role: '审批角色',
      department: '部门归属',
      level: '职级',
      skills: '技能配置',
      tools: '工具配置',
      knowledge_bases: '知识库绑定',
      permissions: '权限配置',
      system_prompt: '系统提示词',
      sop: '作业流程',
      kpi: '考核指标',
    }
    return map[key] || '配置项'
  }
  return key
}

/** Runtime 等级 → 中文标签 */
export const RUNTIME_LEVEL_LABELS: Record<string, string> = {
  draft: '草稿',
  staged: '暂存',
  runnable: '可运行',
  active: '已激活',
}

/** 审批类型 → 中文标签 */
export const APPROVAL_TYPE_LABELS: Record<string, string> = {
  quotation: '报价审批',
  expense: '费用审批',
  procurement: '采购审批',
  leave: '请假审批',
  reimbursement: '报销审批',
  custom: '自定义审批',
}

/** 流程类型 → 中文标签 */
export const PROCESS_TYPE_LABELS: Record<string, string> = {
  approval: '审批流程',
  collaboration: '协作流程',
  business: '业务流程',
  sop: '标准作业流程',
  decision: '决策流程',
  automated: '自动化流程',
  manual: '人工流程',
}

/** 变更类型 → 中文标签 */
export const CHANGE_TYPE_LABELS: Record<string, string> = {
  major: '重大变更',
  minor: '常规变更',
  patch: '补丁修正',
}

/** 工具类型 → 中文标签 */
export const TOOL_TYPE_LABELS: Record<string, string> = {
  mcp: 'MCP 工具',
  script: '脚本工具',
  api: 'API 工具',
}

/** Agent 模板状态 → 中文标签 */
const AGENT_STATUS_LABELS: Record<string, string> = {
  active: '运行中',
  paused: '已暂停',
  inactive: '未启用',
  draft: '草稿',
  archived: '已归档',
  retired: '已下线',
}

/**
 * 将 Agent 模板状态转为中文标签。
 */
export function agentStatusToLabel(status: string | undefined | null): string {
  if (!status) return '未知'
  return AGENT_STATUS_LABELS[status] || status
}

/** 常见权限标识 → 中文标签 */
const PERMISSION_LABELS: Record<string, string> = {
  'read:customers': '读取客户数据',
  'write:customers': '编辑客户数据',
  'read:orders': '读取订单数据',
  'write:orders': '编辑订单数据',
  'read:products': '读取产品数据',
  'write:products': '编辑产品数据',
  'read:opportunities': '读取商机数据',
  'write:opportunities': '编辑商机数据',
  'approve:quotation': '审批报价',
  'approve:expense': '审批费用',
  'read:finance': '读取财务数据',
  'write:finance': '编辑财务数据',
  'read:knowledge': '读取知识库',
  'write:knowledge': '编辑知识库',
  'manage:agents': '管理 AI 员工',
  'manage:workforce': '管理员工团队',
  'view:dashboard': '查看仪表盘',
  'view:audit_logs': '查看审计日志',
}

/** UUID 正则（用于检测原始 ID 是否暴露给用户） */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * 将岗位 ID 转为中文名称。
 * 如果映射表中没有，尝试从字符串中提取有意义的部分。
 */
export function positionToLabel(position: string | undefined | null, agentName?: string): string {
  if (!position) {
    return agentName ? extractRoleFromName(agentName) : '未分配岗位'
  }
  if (POSITION_LABELS[position]) return POSITION_LABELS[position]
  // 尝试去掉 pos_ 前缀后做简单翻译
  if (position.startsWith('pos_')) {
    const key = position.slice(4)
    const fallbackMap: Record<string, string> = {
      sales: '销售',
      finance: '财务',
      service: '客服',
      support: '支持',
    }
    return fallbackMap[key] || (agentName ? extractRoleFromName(agentName) : '数字员工')
  }
  // role-xxx 或 role_xxx 形态：查映射表，未命中则从 agent_name 派生
  if (position.startsWith('role-') || position.startsWith('role_')) {
    const mapped = (ROLE_LABELS as Record<string, string>)[position]
    if (mapped) return mapped
    return agentName ? extractRoleFromName(agentName) : '数字员工'
  }
  // UUID 形态 → 从 agent_name 派生
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(position)) {
    return agentName ? extractRoleFromName(agentName) : '数字员工'
  }
  // 已是中文，直接返回
  if (/[\u4e00-\u9fa5]/.test(position)) return position
  // 其他未识别的英文/标识 → 从 agent_name 派生，最后回退"数字员工"
  return agentName ? extractRoleFromName(agentName) : '数字员工'
}

/** 从 agent_name 中提取角色名（去除"（AI）"/"(AI)"/"- AI"等后缀） */
function extractRoleFromName(agentName: string): string {
  return agentName
    .replace(/（AI）/g, '')
    .replace(/\(AI\)/g, '')
    .replace(/-?\s*AI$/g, '')
    .trim() || '数字员工'
}

/**
 * 将生命周期阶段转为中文标签。
 */
export function lifecycleToLabel(stage: string | undefined | null): string {
  if (!stage) return '未知'
  return LIFECYCLE_LABELS[stage] || stage
}

/**
 * 将生命周期阶段转为颜色类名。
 */
export function lifecycleToColor(stage: string | undefined | null): string {
  if (!stage) return LIFECYCLE_COLORS.draft
  return LIFECYCLE_COLORS[stage] || LIFECYCLE_COLORS.draft
}

/**
 * 将 Agent ID 缩短为可读的短 ID（用于内部标识展示，不应作为主要展示）。
 * 主要展示应使用 agent_name。
 */
export function shortAgentId(agentId: string | undefined | null): string {
  if (!agentId) return '—'
  if (agentId.length <= 12) return agentId
  return agentId.slice(0, 8) + '…'
}

/**
 * 格式化日期字符串，处理空值和无效日期。
 */
export function formatDate(
  dateStr: string | undefined | null,
  opts: { withTime?: boolean } = {},
): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return '—'
  if (opts.withTime) {
    return d.toLocaleString('zh-CN')
  }
  return d.toLocaleDateString('zh-CN')
}

/**
 * 格式化相对时间（如"3分钟前"）。
 */
export function formatRelativeTime(dateStr: string | undefined | null): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return '—'
  const now = Date.now()
  const diff = now - d.getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return d.toLocaleDateString('zh-CN')
}

/**
 * 将审批类型转为中文标签。
 */
export function approvalTypeToLabel(type: string | undefined | null): string {
  if (!type) return '审批事项'
  return APPROVAL_TYPE_LABELS[type] || type
}

/**
 * 将流程类型转为中文标签。
 */
export function processTypeToLabel(type: string | undefined | null): string {
  if (!type) return '未知流程'
  return PROCESS_TYPE_LABELS[type] || type
}

/**
 * 将变更类型转为中文标签。
 */
export function changeTypeToLabel(type: string | undefined | null): string {
  if (!type) return '变更'
  return CHANGE_TYPE_LABELS[type] || type
}

/**
 * 将工具类型转为中文标签。
 */
export function toolTypeToLabel(type: string | undefined | null): string {
  if (!type) return '工具'
  return TOOL_TYPE_LABELS[type] || type
}

/**
 * 将权限标识转为中文标签。
 * 对于 "read:customers" 等格式，先查映射表；未命中则做通用翻译。
 */
export function permissionToLabel(perm: string): string {
  if (!perm) return ''
  if (PERMISSION_LABELS[perm]) return PERMISSION_LABELS[perm]
  // 通用翻译：read→读取、write→编辑、approve→审批、manage→管理、view→查看
  const parts = perm.split(':')
  if (parts.length === 2) {
    const actionMap: Record<string, string> = {
      read: '读取',
      write: '编辑',
      approve: '审批',
      manage: '管理',
      view: '查看',
      delete: '删除',
      create: '创建',
    }
    const action = actionMap[parts[0]] || parts[0]
    const resourceMap: Record<string, string> = {
      customers: '客户数据',
      orders: '订单数据',
      products: '产品数据',
      opportunities: '商机数据',
      finance: '财务数据',
      knowledge: '知识库',
      agents: 'AI 员工',
      workforce: '员工团队',
      dashboard: '仪表盘',
      audit_logs: '审计日志',
      users: '用户',
      enterprises: '企业',
    }
    const resource = resourceMap[parts[1]] || parts[1]
    return `${action}${resource}`
  }
  return perm
}

/**
 * 判断字符串是否为 UUID（用于检测后端是否错误地返回了原始 ID 而非名称）。
 */
export function isUuid(str: string | undefined | null): boolean {
  if (!str) return false
  return UUID_RE.test(str)
}

/**
 * 格式化用户/申请人名称：如果传入的是 UUID（后端未返回名称），返回友好占位文本。
 */
export function formatUserLabel(name: string | undefined | null, fallback = '系统'): string {
  if (!name) return fallback
  if (isUuid(name)) return fallback
  return name
}

/**
 * 格式化事件 payload 中的业务标识：隐藏原始 UUID，仅展示有意义的文本。
 */
export function formatPayloadLabel(value: unknown): string {
  if (!value) return ''
  const str = String(value)
  if (isUuid(str)) return ''  // UUID 不展示
  return str
}
