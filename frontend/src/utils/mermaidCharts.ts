/**
 * mermaidCharts — 将 runtime 结构化数据转换为 mermaid 图语法文本。
 *
 * 供 RuntimeVisualizer 的 #13 协作关系图、#16 组织架构使用，
 * 数据变化时重新生成图文本，配合 MermaidDiagram 自动重渲染。
 */
import type { DepartmentInstance, CollaborationGraph, AgentConfigTemplate } from '@/types'

/** 转义 mermaid 节点标签中的特殊字符，避免破坏语法
 *  覆盖：方括号/花括号/双引号/换行/竖线（破坏 `-->|label|` 边标签）/尖括号/分号 */
function safeLabel(text: string): string {
  return String(text ?? '').replace(/[[\]"{}\n|<>;]/g, ' ').trim()

}

/** 占位/抽象岗位名（来自知识图谱抽取，非真实数字员工岗位），组织架构中不渲染 */
const ORG_NOISE_NAMES = new Set([
  '法定代表人', '直属上级', '接收人', '离职员工', '部门经理', '部门总监',
  '主管', '工程师', '财务', '销售', '经理', '总监', 'CEO / 总经理', 'CEO',
  'CEO/总经理', '验收人员', '制单人', '人事', '行政', '采购', '采购经理', '助理',
  // 知识图谱抽取出的「虚高/变体」岗位名，非真实在编 AI 员工，仅污染组织架构
  '销售总监', '产品总监', '研发总监', '客服主管', '人事经理', '人事行政经理',
  '生产经理', '售前工程师', '售后工程师', '维修工程师', '技术支持',
  // 抽象单字部门名/泛化岗位（dept 为空或为部门名），非具体在编岗位
  '售前', '售后', '客服', '产品', '研发', '生产', '市场', '运营',
  '供应链', '仓储', '物流', '法务', '公关', '品牌', '开发', '测试', '运维',
])

/** 组织架构噪音 agent 名判定：命中命名集合或知识抽取痕迹（事件/流程派生名，
 *  如「产品参数查询（转产品」「客户询问产品参数」），这类不是真实在编岗位。 */
function isOrgNoiseAgent(name: string): boolean {
  if (!name) return true
  if (ORG_NOISE_NAMES.has(name)) return true
  // 知识抽取痕迹：中文括号嵌套、事件/流程动词等（避免误伤「售后服务专员」等真实岗位）
  if (name.includes('（') && name.includes('）')) return true
  return /(查询|询问|参数|报价|询盘|转交|报修|验收)/.test(name)
}

/**
 * 生成「原始 id → mermaid 节点 id」的映射器。
 *
 * 旧实现 ``n${id 清洗非字母数字}`` 会把中文 id（如 ``role_销售代表（AI）``、
 * ``role_财务经理（AI）``）清洗成相同的 ``nrole___``，导致 mermaid 节点 id 碰撞、
 * 图无法渲染。这里改为按出现顺序分配自增序号，保证一一对应、绝不碰撞。
 */
function makeIdMapper(): (id: string) => string {
  const map = new Map<string, string>()
  let i = 0
  return (id: string) => {
    let nid = map.get(id)
    if (nid === undefined) {
      nid = `n${i++}`
      map.set(id, nid)
    }
    return nid
  }
}

/**
 * 组织架构 → mermaid `graph TD` 树（#16）。
 *
 * 后端部门数据经常只有 `parent_dept_id=None`（无真实父子关系），若把所有部门都当作
 * 孤立根，会退化成"虚拟根下挂一片叶子"的扁平星形图，组织层级被完全抹平。
 * 这里按 `level` 自建层级：
 * - 存在名为「高管层」的部门时以其为唯一根，否则用虚拟根「企业」；
 * - level 1 部门作为根的一级子节点；
 * - level ≥ 2 的部门按名称子串/关键字归并到合适的一级部门下（如 行政→人事行政部、
 *   技术支持/IT→研发部），找不到则作为兄弟直接挂到根下。
 * 如此即使所有部门都是根，也能生成一棵正常渲染的层级树。
 */
const VIRTUAL_ROOT = 'virtualRoot'
const L1_KEYWORDS: [string, string][] = [
  ['行政', '人事行政部'],
  ['技术支持', '研发部'],
  ['技术', '研发部'],
  ['IT', '研发部'],
  ['信息', '研发部'],
  ['研发', '研发部'],
  ['客服', '客服部'],
  ['客户', '客服部'],
  ['售后', '客服部'],
  ['产品', '产品部'],
  ['销售', '销售部'],
  ['财务', '财务部'],
  ['人事', '人事行政部'],
  ['生产', '生产部'],
  ['高管', '高管层'],
]

/** 为 level ≥ 2 的部门挑选一个合适的 level 1 父部门（优先关键字，其次名称子串）。 */
function findL1Parent(
  childId: string,
  childName: string,
  departments: DepartmentInstance[],
): string | null {
  const candidates = departments.filter((d) => d.dept_id !== childId && (d.level ?? 1) <= 1)
  for (const [kw, parentName] of L1_KEYWORDS) {
    if (childName.includes(kw)) {
      const hit = candidates.find((d) => d.name === parentName)
      if (hit) return hit.dept_id
    }
  }
  const direct = candidates.find((d) => d.name.includes(childName))
  return direct ? direct.dept_id : null
}

export function buildOrgChart(
  departments: DepartmentInstance[],
  agents?: AgentConfigTemplate[],
): string {
  if (!departments || departments.length === 0) {
    return 'graph TD\n  empty["暂无组织架构数据"]'
  }
  // 顶层：存在「高管层」部门时以其为唯一根；否则以层级最低/无父级的顶层部门
  // （如「示例科技」level 0）为根；都没有才退化为虚拟「企业」根。
  const topDept = departments.find((d) => d.name.includes('高管'))
  const rootDept = topDept ?? departments.find((d) => d.level === 0 || !d.parent_dept_id)
  const byId = new Map(departments.map((d) => [d.dept_id, d]))
  const getId = makeIdMapper()

  // 根 id：有根部门用其 dept_id，否则用虚拟根
  const rootId = rootDept ? rootDept.dept_id : VIRTUAL_ROOT

  // 先计算每个部门的有效父级（真实 dept_id / 根 / null=根）
  const parentOf = new Map<string, string>()
  for (const d of departments) {
    let parent: string
    if (rootDept && d.dept_id === rootDept.dept_id) {
      parent = '__ROOT__'
    } else if (d.parent_dept_id && byId.has(d.parent_dept_id!)) {
      parent = d.parent_dept_id! // 有真实父子关系时优先使用
    } else if ((d.level ?? 1) >= 2) {
      parent = findL1Parent(d.dept_id, d.name, departments) ?? rootId
    } else {
      parent = rootId
    }
    parentOf.set(d.dept_id, parent)
  }

  // 一级部门（根的直接子级）——作为「一块一块」的块
  const l1 = departments.filter((d) => parentOf.get(d.dept_id) === rootId)

  // 每个部门的直接子部门（用于在块内平铺）
  const childrenOf = new Map<string, string[]>()
  for (const d of departments) {
    const p = parentOf.get(d.dept_id)
    if (!p || p === '__ROOT__' || p === rootId) continue
    const arr = childrenOf.get(p) ?? []
    arr.push(d.dept_id)
    childrenOf.set(p, arr)
  }

  // 部门名 → 部门实例（用于把具体 AI 员工挂到正确的部门块下）
  const byName = new Map<string, DepartmentInstance>()
  for (const d of departments) byName.set(d.name, d)

  // 鲁棒匹配：exact → 包含（双向，仅匹配 L1 部门）→ null
  // （演示/编译数据中 agent.department 常为空或与部门名不完全一致，需尽量归位）
  const matchDept = (deptName: string): DepartmentInstance | null => {
    if (!deptName) return null
    const exact = byName.get(deptName)
    if (exact) return exact
    const contains = departments.find(
      (d) => d.level === 1 && (deptName.includes(d.name) || d.name.includes(deptName)),
    )
    return contains || null
  }
  // 按岗位名关键词推断 L1 部门（用于 department 为空时尽量归位）
  const DEPT_KEYWORDS: [string, string][] = [
    ['财务', '财务部'], ['会计', '财务部'], ['出纳', '财务部'],
    ['工程师', '研发部'], ['研发', '研发部'], ['技术', '研发部'], ['开发', '研发部'],
    ['销售', '销售部'], ['售前', '销售部'], ['客户经理', '销售部'], ['市场', '销售部'],
    ['客服', '客服部'], ['售后', '客服部'],
    ['产品', '产品部'], ['项目', '产品部'],
    ['人事', '人事行政部'], ['行政', '人事行政部'], ['HR', '人事行政部'],
    ['生产', '生产部'], ['质检', '生产部'], ['工艺', '生产部'], ['制造', '生产部'],
  ]
  const inferDeptName = (name: string): string | null => {
    if (!name) return null
    const hit = DEPT_KEYWORDS.find(([kw]) => name.includes(kw))
    return hit ? hit[1] : null
  }
  // 计算某部门实例归属的 L1 块 dept_id（沿 parentOf 上溯到根的一级子）
  const blockOfDept = (d: DepartmentInstance): string | null => {
    let blockId = parentOf.get(d.dept_id)
    if (blockId === rootId || blockId === '__ROOT__') blockId = d.dept_id
    else {
      let cursor = d.dept_id
      while (cursor && parentOf.get(cursor) !== rootId && parentOf.get(cursor) !== '__ROOT__') {
        cursor = parentOf.get(cursor)!
      }
      blockId = cursor || d.dept_id
    }
    if (!blockId || blockId === VIRTUAL_ROOT) return null
    return blockId
  }

  // Agent → 归属块；匹配不到或推断不到的去「未分组」兜底块
  const agentNodesByBlock = new Map<string, string[]>() // blockId -> mermaid 节点 id 行
  const unmatchedAgents: string[] = []
  for (const a of agents ?? []) {
    if (isOrgNoiseAgent(a?.agent_name)) continue
    const nodeLine = `    ${getId(a.agent_id || a.agent_name)}["${safeLabel(a.agent_name)}"]`
    let blockId: string | null = null
    const dept = matchDept(a.department ?? '')
    if (dept) blockId = blockOfDept(dept)
    if (!blockId) {
      const inferred = inferDeptName(a.agent_name)
      if (inferred) {
        const d2 = byName.get(inferred)
        if (d2) blockId = blockOfDept(d2)
      }
    }
    if (!blockId) { unmatchedAgents.push(nodeLine); continue }
    const arr = agentNodesByBlock.get(blockId) ?? []
    arr.push(nodeLine)
    agentNodesByBlock.set(blockId, arr)
  }

  const lines: string[] = ['flowchart TB']
  // 根节点：有真实根部门（高管层/示例科技 level0）时以其为实际根实体，
  // 否则才退化为虚拟「企业」根。修复：此前当存在 level0 部门但无高管层时，
  // 根节点仍用虚拟「企业」，而边却指向未声明的 rootDept（生成 n35 裸节点）。
  const rootNode = rootDept
    ? `${getId(rootDept.dept_id)}(["${safeLabel(rootDept.name)}"])`
    : `${VIRTUAL_ROOT}(("企业"))`
  lines.push(`  ${rootNode}`)

  // 一级部门以 subgraph 块呈现：每块一个部门卡片 + 其直接子部门 + 具体 AI 员工平铺在内
  for (const d of l1) {
    if (!d) continue
    const blockId = getId(d.dept_id)
    lines.push(`  subgraph ${blockId}_g["${safeLabel(d.name)}"]`)
    lines.push(`    ${blockId}(["${safeLabel(d.name)}"])`)
    for (const cid of childrenOf.get(d.dept_id) ?? []) {
      const c = byId.get(cid)
      if (!c) continue
      lines.push(`    ${getId(cid)}["${safeLabel(c.name)}"]`)
    }
    // 具体 AI 员工节点（挂在本部门块下）
    for (const agentLine of agentNodesByBlock.get(d.dept_id) ?? []) {
      lines.push(agentLine)
    }
    lines.push('  end')
    // 根 -> 部门块内节点（指向 subgraph 内部节点，mermaid 渲染更稳定）
    lines.push(`  ${rootId === VIRTUAL_ROOT ? VIRTUAL_ROOT : getId(rootId)} --> ${blockId}`)
  }

  // 未匹配/未分组的具体 AI 员工兜底块（保证全部 AI 员工都有归属、都能看到）
  if (unmatchedAgents.length > 0) {
    const fbId = getId('__unmatched__')
    lines.push(`  subgraph ${fbId}_g["未分组"]`)
    // 块内先声明块节点，保证根->块边有合法目标（否则 mermaid 生成未定义裸节点，如 n57）
    lines.push(`    ${fbId}(["未分组"])`)
    for (const line of unmatchedAgents) lines.push(line)
    lines.push('  end')
    lines.push(`  ${rootId === VIRTUAL_ROOT ? VIRTUAL_ROOT : getId(rootId)} --> ${fbId}`)
  }

  return lines.join('\n')
}

/**
 * 协作关系图 → mermaid `flowchart LR`（#13）。
 * 按部门聚簇（subgraph），节点为岗位，边为协作关系，默认更清晰、可缩放。
 */
export function buildCollaborationChart(graph: CollaborationGraph): string {
  const nodes = (graph?.nodes ?? []) as { id?: string; name?: string; label?: string; department?: string }[]
  const edges = graph?.edges ?? []

  if (nodes.length === 0) {
    return 'flowchart LR\n  empty["暂无协作关系数据"]'
  }

  // 过滤占位/抽象节点（与 Agent 状态视图的 NON_AGENT_NAMES 一致）：
  // 这些节点来自知识图谱抽取而非真实数字员工岗位（法定代表人/直属上级/离职员工/
  // 部门经理/部门总监/主管/经理/总监/工程师/财务/销售 等），以及部门名（生产/产品/
  // 研发/客服…，部门名已由 subgraph 簇标签呈现）。剔除后协作网更紧凑、更聚焦真实岗位，
  // 避免整图默认过小、需要滚动才能看清。
  const NOISE_NODE_NAMES = new Set([
    '法定代表人', '直属上级', '接收人', '离职员工', '部门经理', '部门总监',
    '主管', '工程师', '财务', '销售', '经理', '总监', 'CEO / 总经理',
    'CEO', '验收人员', '制单人', '人事', '行政', '生产', '产品', '研发',
    '售前', '客服', '技术支持', '采购', '采购专员', '采购经理', '总经办', '助理',
  ])

  // 防御性上限：协作关系图边数过多时 mermaid 渲染会冻结浏览器主线程（端到端测试发现）。
  // 后端已改为 O(n) 中心辐射模型，正常边数远小于此上限；此处兜底防止异常数据导致页面卡死。
  const MAX_EDGES = 400
  const truncated = edges.length > MAX_EDGES

  const lines: string[] = ['flowchart LR']
  const deptGroups = new Map<string, string[]>() // deptName -> nodeIds
  const nodeName = new Map<string, string>()
  const getId = makeIdMapper()

  for (const n of nodes) {
    const id = n.id ?? ''
    if (!id) continue
    const name = n.name || n.label || id
    if (NOISE_NODE_NAMES.has(name)) continue // 剔除占位/部门名节点
    nodeName.set(id, name)
    const dept = n.department || '其他'
    if (!deptGroups.has(dept)) deptGroups.set(dept, [])
    deptGroups.get(dept)!.push(id)
  }

  // 剔除空簇（如全部为占位节点导致的空「其他」）
  for (const [dept, ids] of deptGroups) {
    if (ids.length === 0) deptGroups.delete(dept)
  }

  if (deptGroups.size === 0) {
    return 'flowchart LR\n  empty["暂无协作关系数据"]'
  }

  // 单部门时不套 subgraph，直接平铺避免样式冗余
  if (deptGroups.size === 1) {
    for (const [, ids] of deptGroups) {
      for (const id of ids) {
        lines.push(`  ${getId(id)}["${safeLabel(nodeName.get(id) ?? id)}"]`)
      }
    }
  } else {
    for (const [dept, ids] of deptGroups) {
      lines.push(`  subgraph ${getId(dept)}["${safeLabel(dept)}"]`)
      for (const id of ids) {
        lines.push(`    ${getId(id)}["${safeLabel(nodeName.get(id) ?? id)}"]`)
      }
      lines.push('  end')
    }
  }

  // 边：仅保留两端节点均已声明的边，避免悬空；超过 MAX_EDGES 时截断，防止渲染卡死
  const validIds = new Set(nodeName.keys())
  const seen = new Set<string>()
  let edgeCount = 0
  for (const e of edges) {
    if (edgeCount >= MAX_EDGES) break
    if (!validIds.has(e.source_id) || !validIds.has(e.target_id)) continue
    const key = `${e.source_id}->${e.target_id}`
    if (seen.has(key)) continue
    seen.add(key)
    edgeCount += 1
    const label = e.context || relationToLabel(e.relation)
    lines.push(`  ${getId(e.source_id)} -->|${safeLabel(label)}| ${getId(e.target_id)}`)
  }

  if (truncated) {
    lines.push(`  note["已显示前 ${edgeCount} 条协作关系（共 ${edges.length} 条）"]`)
  }

  return lines.join('\n')
}

/** 关系枚举 → 中文标签 */
function relationToLabel(relation?: string): string {
  switch (relation) {
    case 'reports_to':
      return '汇报'
    case 'approves_for':
      return '审批'
    case 'hands_off_to':
      return '交接'
    case 'collaborates_with':
    default:
      return '协作'
  }
}