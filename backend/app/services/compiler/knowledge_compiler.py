"""Knowledge Compiler —— 第二级编译器（PRD §4.3）。

输入：InformationCompileOutput（结构化信息条目）
处理：实体对齐 + 关系抽取 + 知识图谱构建 + 向量化
输出：KnowledgeCompileOutput（图谱节点 + 边 + 向量集合）
置信度：基于实体去重率和关系抽取覆盖率
"""
import logging

from app.services.compiler.base import (
    CompilerBase,
    CompilationContext,
    CompilationResult,
    Stage,
)
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.compiler import (
    InformationCompileOutput,
    InformationEntry,
    GraphNode,
    GraphEdge,
    KnowledgeCompileOutput,
)
from app.services.llm_service import llm_service
from app.services import prompt_security
from app.services.compiler.base import safe_json_parse

logger = logging.getLogger(__name__)


class KnowledgeCompiler(CompilerBase):
    """Knowledge Compiler。

    将 Information 级的结构化信息条目转化为知识图谱。
    流程：实体对齐（去重）→ 关系抽取（LLM）→ 图构建 → 向量化
    """

    stage = Stage.KNOWLEDGE.value

    async def compile(self, ctx: CompilationContext) -> CompilationResult:
        """执行 Knowledge 级编译。"""
        # 从上游获取 Information 级输出
        info_output = ctx.upstream.get("information")
        if info_output is None:
            self.logger.warning("Knowledge 编译：缺少上游 information 产物")
            return CompilationResult(
                stage=self.stage,
                output=KnowledgeCompileOutput(enterprise_id=ctx.enterprise_id),
                confidence=0.0,
                discovered_summary="缺少上游信息层产物",
            )

        if isinstance(info_output, InformationCompileOutput):
            entries = info_output.entries
        elif isinstance(info_output, dict):
            entries = [InformationEntry(**e) for e in info_output.get("entries", [])]
        else:
            entries = []

        self.logger.info(f"Knowledge 编译开始: enterprise={ctx.enterprise_id}, entries={len(entries)}")

        # 1. 实体对齐（去重）
        aligned_nodes = self._align_entities(entries)
        total_before = len(entries)
        total_after = len(aligned_nodes)

        # 2. 关系抽取
        edges = await self._extract_relations(ctx, aligned_nodes)

        # 3. 构建知识图谱（写入 GraphStore）
        graph_store = PGJSONBGraphStore(ctx.db, ctx.enterprise_id)
        for node in aligned_nodes:
            await graph_store.add_node(node)
        for edge in edges:
            await graph_store.add_edge(edge)
        await graph_store.save()

        # 4. 向量化（复用 vector_store，collection 名按 enterprise 维度）
        vector_collection = f"enterprise_{ctx.enterprise_id}"

        # 计算置信度
        confidence = self.calculate_confidence(total_before, total_after, base=0.4, max_bonus=0.6)

        output = KnowledgeCompileOutput(
            enterprise_id=ctx.enterprise_id,
            nodes=aligned_nodes,
            edges=edges,
            vector_collection=vector_collection,
            confidence=confidence,
        )

        summary = self._generate_summary(aligned_nodes, edges)

        return CompilationResult(
            stage=self.stage,
            output=output,
            confidence=confidence,
            discovered_summary=summary,
            discovered_count=len(aligned_nodes),
        )

    def _align_entities(self, entries: list[InformationEntry]) -> list[GraphNode]:
        """实体对齐：同名同类型实体合并，保留最高置信度。

        MVP 策略：按 (entry_type, name) 去重，属性合并。
        P1 可引入嵌入向量相似度对齐。
        """
        node_map: dict[str, GraphNode] = {}
        for entry in entries:
            key = f"{entry.entry_type}::{entry.name.lower().strip()}"
            if key in node_map:
                # 合并属性
                existing = node_map[key]
                merged_attrs = {**existing.attributes, **entry.attributes}
                node_map[key] = existing.model_copy(update={
                    "attributes": merged_attrs,
                    "confidence": max(existing.confidence, entry.confidence),
                })
            else:
                # 优先复用信息编译器下发的 entry_id 作为 node_id，保证跨级父子关系
                # （如 Department.parent_id → 公司根节点）的 ID 能正确对齐；仅当没有
                # 稳定的 entry_id（如 filename fallback）时才现场生成。
                node_id = entry.entry_id or self._make_entry_id(entry.entry_type.lower())
                node_map[key] = GraphNode(
                    node_id=node_id,
                    node_type=entry.entry_type,
                    name=entry.name,
                    attributes={
                        **entry.attributes,
                        "source_file": entry.source_file,
                        "file_type": entry.file_type,
                    },
                    confidence=entry.confidence,
                )
        return list(node_map.values())

    async def _extract_relations(
        self, ctx: CompilationContext, nodes: list[GraphNode]
    ) -> list[GraphEdge]:
        """关系抽取：使用 LLM 从节点描述中推断关系。

        策略：
        - 基于规则的显式关系（如 Role → Department 的 BELONGS_TO）
        - LLM 推断的隐式关系
        """
        edges: list[GraphEdge] = []

        # 基于规则的关系抽取
        edges.extend(self._rule_based_relations(nodes))

        # LLM 关系抽取（节点数较多时分批处理）
        if len(nodes) <= 50:
            llm_edges = await self._llm_relation_extraction(ctx, nodes)
            edges.extend(llm_edges)
        else:
            # 分批处理
            batch_size = 50
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i + batch_size]
                llm_edges = await self._llm_relation_extraction(ctx, batch)
                edges.extend(llm_edges)

        # 去重
        seen = set()
        unique_edges: list[GraphEdge] = []
        for e in edges:
            key = f"{e.source_id}->{e.target_id}:{e.relation}"
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        return unique_edges

    def _rule_based_relations(self, nodes: list[GraphNode]) -> list[GraphEdge]:
        """基于规则的关系抽取。

        规则：
        - Role 节点的 department 属性 → BELONGS_TO Department
        - Role 节点的 kpi_ids 属性 → OWES KPI
        - Role 节点的 permission_ids 属性 → HAS_PERMISSION Permission
        """
        edges: list[GraphEdge] = []
        node_by_name: dict[str, GraphNode] = {}
        for n in nodes:
            node_by_name[f"{n.node_type}::{n.name.lower()}"] = n

        for node in nodes:
            if node.node_type == EntityType.ROLE.value:
                dept_name = node.attributes.get("department", "")
                if dept_name:
                    dept_key = f"{EntityType.DEPARTMENT.value}::{dept_name.lower()}"
                    dept = node_by_name.get(dept_key)
                    if dept:
                        edges.append(GraphEdge(
                            source_id=node.node_id,
                            target_id=dept.node_id,
                            relation=RelationType.BELONGS_TO.value,
                        ))
        return edges

    async def _llm_relation_extraction(
        self, ctx: CompilationContext, nodes: list[GraphNode]
    ) -> list[GraphEdge]:
        """使用 LLM 抽取实体间关系。

        用户输入内容经 prompt_security.wrap_untrusted 包裹。
        """
        if len(nodes) < 2:
            return []

        valid_relations = [r.value for r in RelationType]
        node_desc = [
            {"id": n.node_id, "type": n.node_type, "name": n.name,
             "attrs": {k: v for k, v in list(n.attributes.items())[:3]}}
            for n in nodes[:30]  # 限制 LLM 输入规模
        ]

        system_prompt = (
            "你是企业知识图谱关系抽取专家。分析给定实体列表，识别它们之间的关系。"
            f"可选关系类型：{', '.join(valid_relations)}。"
            "请以 JSON 数组格式输出，每个关系包含：source_id、target_id、relation。"
            "仅输出 JSON，不要其他文字。"
        )

        # 安全包裹用户输入
        user_message = prompt_security.wrap_untrusted(
            f"实体列表：{node_desc}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            response = await llm_service.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=4096,
            )
            relations_data = safe_json_parse(response)
            if not isinstance(relations_data, list):
                return []
        except Exception as e:
            self.logger.warning(f"LLM 关系抽取失败: {e}")
            return []

        node_ids = {n.node_id for n in nodes}
        edges: list[GraphEdge] = []
        for item in relations_data:
            if not isinstance(item, dict):
                continue
            src = item.get("source_id", "")
            tgt = item.get("target_id", "")
            rel = item.get("relation", "")
            if src in node_ids and tgt in node_ids and rel in valid_relations:
                edges.append(GraphEdge(
                    source_id=src,
                    target_id=tgt,
                    relation=rel,
                ))
        return edges

    def _generate_summary(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
        """生成发现摘要。"""
        from app.services.compiler.i18n import entity_label, relation_label
        type_counts: dict[str, int] = {}
        for n in nodes:
            type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1
        rel_counts: dict[str, int] = {}
        for e in edges:
            rel_counts[e.relation] = rel_counts.get(e.relation, 0) + 1

        parts = [f"构建知识图谱：{len(nodes)} 节点、{len(edges)} 关系"]
        if type_counts:
            type_str = "、".join(
                f"{entity_label(t)} {c}"
                for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
            )
            parts.append(f"实体分布：{type_str}")
        if rel_counts:
            rel_str = "、".join(
                f"{relation_label(r)} {c}"
                for r, c in sorted(rel_counts.items(), key=lambda x: -x[1])
            )
            parts.append(f"关系分布：{rel_str}")
        return "；".join(parts)
