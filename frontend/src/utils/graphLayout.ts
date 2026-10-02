/**
 * graphLayout — 基于 dagre 的分层/树状布局工具。
 *
 * 用于知识图谱、组织架构等 ReactFlow 图的自动布局，
 * 替代手动环形布局，解决节点重叠（"折叠到一起"）问题。
 */
import dagre from '@dagrejs/dagre'
import type { Node, Edge } from 'reactflow'

export interface DagreLayoutOptions {
  /** 布局方向：TB=自上而下（金字塔/树），LR=从左到右 */
  direction?: 'TB' | 'LR'
  /** 节点间距 */
  nodeSep?: number
  /** 层间距 */
  rankSep?: number
  /** 节点宽度 */
  nodeWidth?: number
  /** 节点高度 */
  nodeHeight?: number
}

/**
 * 使用 dagre 对 ReactFlow 节点进行分层布局。
 * 返回带新 position 的节点数组。
 */
export function layoutWithDagre(
  nodes: Node[],
  edges: Edge[],
  options: DagreLayoutOptions = {},
): Node[] {
  const {
    direction = 'TB',
    nodeSep = 60,
    rankSep = 100,
    nodeWidth = 160,
    nodeHeight = 50,
  } = options

  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({
    rankdir: direction,
    nodesep: nodeSep,
    ranksep: rankSep,
    marginx: 40,
    marginy: 40,
  })

  // 添加节点
  for (const node of nodes) {
    g.setNode(node.id, {
      width: node.width ?? nodeWidth,
      height: node.height ?? nodeHeight,
    })
  }

  // 添加边
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target)
  }

  // 执行布局
  dagre.layout(g)

  // 将布局结果写回节点
  return nodes.map((node) => {
    const positioned = g.node(node.id)
    if (!positioned) return node
    return {
      ...node,
      position: {
        x: positioned.x - (positioned.width ?? nodeWidth) / 2,
        y: positioned.y - (positioned.height ?? nodeHeight) / 2,
      },
    }
  })
}
