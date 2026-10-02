/**
 * AgentCanvas · 智能体编排画布
 *
 * 基于 ReactFlow 的可视化工作流编辑组件，方案C 工作流画布动效：
 * - 节点按状态着色：completed / processing / failed / idle
 * - processing 节点叠加 .wf-node-active 脉冲动画
 * - 连线使用品牌色 #1E3A5F，活跃连线叠加 .flow-dash 流动虚线
 * - 节点内显示：图标 + 名称 + 描述 + 状态点
 *
 * 组件采用"受控"模式：父级持有 nodes/edges 状态，本组件只负责渲染与事件转发。
 */
import { useMemo } from 'react'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeTypes,
  type NodeProps,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type NodeMouseHandler,
  MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import {
  FileText,
  Search,
  Brain,
  Wrench,
  MessageSquare,
  GitBranch,
  Repeat,
  FolderSearch,
  ShieldCheck,
  Hammer,
  TestTube,
  Database,
  type LucideIcon,
} from 'lucide-react'

// ===== 类型定义 =====

/** 智能体编排节点类型 */
export type AgentNodeType =
  | 'input'
  | 'retrieve'
  | 'reason'
  | 'tool'
  | 'output'
  | 'condition'
  | 'loop'
  // P0-1c/S1: LangGraph 8 节点状态机类型
  | 'planner'
  | 'scanner'
  | 'approval'
  | 'parser'
  | 'vectorizer'
  | 'builder'
  | 'tester'

/** 节点运行状态 */
export type NodeStatus = 'idle' | 'processing' | 'completed' | 'failed'

/** 节点配置参数 */
export interface AgentNodeConfig {
  model?: string
  temperature?: number
  systemPrompt?: string
  maxTokens?: number
  knowledgeBase?: string
  topK?: number
  [key: string]: unknown
}

/** 节点数据（ReactFlow Node.data） */
export interface AgentNodeData {
  label: string
  type: AgentNodeType
  status: NodeStatus
  /** 显式指定图标，缺省时根据 type 自动取 NODE_TYPE_META.icon */
  icon?: LucideIcon
  description?: string
  config?: AgentNodeConfig
  [key: string]: unknown
}

/** 完整节点类型（带泛型数据） */
export type AgentFlowNode = Node<AgentNodeData>

// ===== 节点元数据：左侧节点库 + 节点渲染共用 =====

export const NODE_TYPE_META: Record<
  AgentNodeType,
  {
    label: string
    description: string
    icon: LucideIcon
    defaultConfig?: AgentNodeConfig
  }
> = {
  input: {
    label: '输入',
    description: '用户消息、文件输入',
    icon: FileText,
  },
  retrieve: {
    label: '检索',
    description: '知识库向量检索',
    icon: Search,
    defaultConfig: { knowledgeBase: '企业知识库', topK: 5 },
  },
  reason: {
    label: '推理',
    description: 'LLM 推理与决策',
    icon: Brain,
    defaultConfig: {
      // P0-S2: 默认模型与后端 .env 示例保持一致（DeepSeek）
      model: 'deepseek/deepseek-chat',
      temperature: 0.7,
      systemPrompt: '你是一个专业的企业客服助手。根据知识库内容回答用户问题。如果不知道，请如实告知。',
      maxTokens: 2048,
    },
  },
  tool: {
    label: '工具',
    description: '调用外部 API',
    icon: Wrench,
  },
  output: {
    label: '输出',
    description: '回复用户',
    icon: MessageSquare,
  },
  condition: {
    label: '条件',
    description: '条件分支',
    icon: GitBranch,
  },
  loop: {
    label: '循环',
    description: '循环执行',
    icon: Repeat,
  },
  // P0-1c/S1: LangGraph 7 节点状态机
  planner: {
    label: '规划器',
    description: '创建 Agent 记录，初始化构建',
    icon: Brain,
  },
  scanner: {
    label: '文件扫描',
    description: '扫描文件夹，识别文件类型',
    icon: FolderSearch,
  },
  approval: {
    label: '人工审批',
    description: 'HITL 检查点，审批扫描结果',
    icon: ShieldCheck,
  },
  parser: {
    label: '解析分块',
    description: '解析文件 + 结构感知分块',
    icon: FileText,
  },
  vectorizer: {
    label: '向量化',
    description: '向量化存入 ChromaDB',
    icon: Database,
  },
  builder: {
    label: '构建器',
    description: '生成 system_prompt + skills',
    icon: Hammer,
  },
  tester: {
    label: '测试器',
    description: '测试 Agent 是否可用',
    icon: TestTube,
  },
}

// ===== 状态样式映射（方案C 节点状态着色） =====

interface StatusStyle {
  border: string
  bg: string
  iconWrap: string
  iconColor: string
  labelColor: string
  descriptionColor: string
  dotClass: string
  /** 是否叠加 wf-node-active 脉冲动画 */
  pulse: boolean
}

const statusConfig: Record<NodeStatus, StatusStyle> = {
  idle: {
    border: 'border-border-default',
    bg: 'bg-surface',
    iconWrap: 'bg-elevated',
    iconColor: 'text-brand-500',
    labelColor: 'text-text-primary',
    descriptionColor: 'text-text-tertiary',
    dotClass: 'dot-muted',
    pulse: false,
  },
  processing: {
    border: 'border-brand-500',
    bg: 'bg-brand-50',
    iconWrap: 'bg-brand-100',
    iconColor: 'text-brand-500',
    labelColor: 'text-brand-500',
    descriptionColor: 'text-text-secondary',
    dotClass: 'dot-info',
    pulse: true,
  },
  completed: {
    border: 'border-success',
    bg: 'bg-success/10',
    iconWrap: 'bg-success/10',
    iconColor: 'text-success',
    labelColor: 'text-text-primary',
    descriptionColor: 'text-text-tertiary',
    dotClass: 'dot-success',
    pulse: false,
  },
  failed: {
    border: 'border-error',
    bg: 'bg-error/10',
    iconWrap: 'bg-error/10',
    iconColor: 'text-error',
    labelColor: 'text-text-primary',
    descriptionColor: 'text-text-tertiary',
    dotClass: 'dot-error',
    pulse: false,
  },
}

// ===== 自定义节点组件 =====

function AgentNodeComponent({ data, selected }: NodeProps<AgentNodeData>) {
  const meta = NODE_TYPE_META[data.type]
  const Icon = data.icon || meta.icon
  const cfg = statusConfig[data.status]
  const description = data.description || meta.description

  return (
    <div
      className={[
        'relative px-3 py-3 rounded-lg border-2 theme-transition min-w-[180px]',
        cfg.border,
        cfg.bg,
        selected ? 'shadow-lift' : 'shadow-soft',
        cfg.pulse ? 'wf-node-active' : '',
      ].join(' ')}
    >
      {/* ReactFlow 连接端口：顶部入口 + 底部出口 */}
      <Handle
        type="target"
        position={Position.Top}
        id="in"
        style={{ top: -4 }}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="out"
        style={{ bottom: -4 }}
      />
      <div className="flex items-center gap-2.5">
        <span
          className={[
            'w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0',
            cfg.iconWrap,
          ].join(' ')}
        >
          <Icon className={`h-4 w-4 ${cfg.iconColor}`} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className={`text-sm font-medium truncate ${cfg.labelColor}`}>{data.label}</div>
          <div className={`text-xs truncate ${cfg.descriptionColor}`}>{description}</div>
        </div>
        <span className={`dot ${cfg.dotClass}`} aria-hidden="true" />
      </div>
    </div>
  )
}

// 节点类型注册（useMemo 固定引用避免 ReactFlow 警告）
const nodeTypes: NodeTypes = {
  agentNode: AgentNodeComponent,
}

// 默认边配置：品牌色 + 箭头 + 流动虚线动画
export const defaultEdgeOptions = {
  type: 'default',
  markerEnd: { type: MarkerType.ArrowClosed, color: '#1E3A5F' },
  style: { stroke: '#1E3A5F', strokeWidth: 2 },
  animated: true,
}

// ===== 组件 Props =====

export interface AgentCanvasProps {
  nodes: AgentFlowNode[]
  edges: Edge[]
  onNodesChange?: OnNodesChange
  onEdgesChange?: OnEdgesChange
  onConnect?: OnConnect
  onNodeClick?: NodeMouseHandler
  onPaneClick?: () => void
  className?: string
  height?: number | string
}

// ===== 主组件 =====

export function AgentCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onNodeClick,
  onPaneClick,
  className = '',
  height = '100%',
}: AgentCanvasProps) {
  const memoNodeTypes = useMemo(() => nodeTypes, [])

  return (
    <div className={`relative w-full h-full ${className}`} style={{ height }}>
      {/* ReactFlow 主题覆盖：让画布融入方案B暖白纸设计系统 */}
      <style>{`
        .react-flow__attribution { display: none; }
        .react-flow__pane {
          background-color: var(--bg-surface-2);
          background-image: radial-gradient(circle, var(--border-default) 1px, transparent 1px);
          background-size: 20px 20px;
        }
        .react-flow__controls {
          background: var(--bg-surface);
          border: 1px solid var(--border-default);
          border-radius: 8px;
          overflow: hidden;
          box-shadow: 0 1px 2px rgba(30,58,95,0.04), 0 4px 16px rgba(30,58,95,0.05);
        }
        .react-flow__controls-button {
          background: var(--bg-surface);
          color: var(--text-secondary);
          border-bottom: 1px solid var(--border-subtle);
        }
        .react-flow__controls-button:hover { background: var(--bg-elevated); }
        .react-flow__controls-button svg { fill: var(--text-secondary); }
        /* 连线：品牌色 + 活跃连线叠加 flow-dash 流动虚线动画 */
        .react-flow__edge-path { stroke: #1E3A5F; stroke-width: 2; }
        .react-flow__edge.animated .react-flow__edge-path {
          stroke-dasharray: 6 6;
          animation: flowDash 1.2s linear infinite;
        }
        .react-flow__edge.selected .react-flow__edge-path { stroke-width: 3; }
        /* 端口：品牌色小圆点 */
        .react-flow__handle {
          width: 8px; height: 8px;
          background: var(--brand);
          border: 2px solid var(--bg-surface);
          border-radius: 9999px;
        }
        .react-flow__handle:hover { background: var(--brand-strong); }
        .react-flow__node { cursor: pointer; }
      `}</style>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={memoNodeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.4}
        maxZoom={1.5}
        nodesDraggable
        nodesConnectable
        elementsSelectable
        deleteKeyCode={null}
        multiSelectionKeyCode={null}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(30,58,95,0.10)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  )
}

export default AgentCanvas
