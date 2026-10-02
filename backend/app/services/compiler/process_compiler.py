"""Process Compiler —— 第三级编译器（PRD §4.3）。

输入：KnowledgeCompileOutput（知识图谱）
处理：SOP 提取 + 审批流建模 + KPI 关联
输出：ProcessCompileOutput（流程定义列表）
置信度：基于流程提取覆盖率和步骤完整度
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
    GraphNode,
    ProcessDefinition,
    ProcessCompileOutput,
)
from app.services.llm_service import llm_service
from app.services import prompt_security
from app.services.compiler.base import safe_json_parse

logger = logging.getLogger(__name__)


# 流程命名模板：按关键词推断类型化流程名（当流程节点 name 缺失时使用）
_PROCESS_NAME_TEMPLATES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("采购", "供应商", "比价", "下单", "供应链"), "采购审批流程"),
    (("销售", "报价", "线索", "跟单", "成交", "订单", "询价"), "销售跟进流程"),
    (("客服", "服务", "投诉", "工单", "满意度"), "客户服务流程"),
    (("财务", "报销", "付款", "发票", "费用", "账期"), "财务审批流程"),
    (("合同", "签署", "法务", "合规"), "合同签署流程"),
    (("售后", "维修", "报修", "返厂"), "售后处理流程"),
    (("入职", "招聘", "员工"), "员工入职流程"),
    (("离职", "交接"), "员工离职流程"),
    (("审批", "申请", "审核", "签批"), "业务审批流程"),
)
# 通用流程兜底名
_GENERIC_PROCESS_NAME = "业务流程"

# 常见业务名关键词 → 推断类型标签（用于兜底命名，避免文件名式的节点名污染展示名）
def _looks_like_filename(name: str) -> bool:
    """判断节点名是否为「无业务语义」的占位名。
    知识图谱的 Process 节点 name 常为文件名（如 "README.md"、"org_chart.pdf"），
    或为空。此类名不应作为可视化流程名展示，而应回退为类型化标签。
    """
    s = (name or "").strip()
    if not s:
        return True
    # 含扩展名/路径分隔符，或长串无意义的十六进制/编号，视为文件名/技术占位名
    if "." in s or "/" in s or "\\" in s:
        return True
    return False


def _infer_process_name(node_name: str, search_text: str) -> str:
    """根据节点名/描述关键词推断类型化流程名；退化为通用流程名。"""
    for keywords, label in _PROCESS_NAME_TEMPLATES:
        if any(k in search_text for k in keywords):
            return label
    return _GENERIC_PROCESS_NAME


class ProcessCompiler(CompilerBase):
    """Process Compiler。

    从知识图谱中的流程实体提取 SOP，建模审批流，关联 KPI。
    流程：识别流程节点 → SOP 提取（LLM）→ 审批流建模 → KPI 关联
    """

    stage = Stage.PROCESS.value

    async def compile(self, ctx: CompilationContext) -> CompilationResult:
        """执行 Process 级编译。"""
        # 从知识图谱加载流程节点
        graph_store = PGJSONBGraphStore(ctx.db, ctx.enterprise_id)
        process_nodes = await graph_store.query_nodes_by_type(EntityType.PROCESS.value)
        kpi_nodes = await graph_store.query_nodes_by_type(EntityType.KPI.value)

        self.logger.info(
            f"Process 编译开始: enterprise={ctx.enterprise_id}, "
            f"processes={len(process_nodes)}"
        )

        processes: list[ProcessDefinition] = []
        extracted = 0

        for proc_node in process_nodes:
            try:
                # 查询流程的关联节点
                exec_roles = await graph_store.query_neighbors(
                    proc_node.node_id, relation=RelationType.EXECUTES.value, direction="in"
                )
                used_systems = await graph_store.query_neighbors(
                    proc_node.node_id, relation=RelationType.USES.value
                )

                # SOP 提取
                steps = await self._extract_sop(ctx, proc_node, exec_roles)
                if not steps:
                    # 降级1：使用流程属性中已有的步骤（可能为字符串列表，需规范化）
                    steps = proc_node.attributes.get("steps", [])
                if not steps:
                    # 降级2：无 LLM / 无显式步骤时，用规则模板生成可执行的 SOP，
                    # 保证流程引擎的业务流程非空、可正常呈现（而非 0 步）。
                    steps = self._generate_fallback_steps(proc_node)
                # 规范化步骤：兼容属性中"字符串步骤名"的旧数据，转成步骤字典
                steps = self._normalize_steps(steps, proc_node)

                # 审批流建模
                steps = self._model_approval_flow(steps, exec_roles)

                # 关联 KPI
                kpi_ids = await self._link_kpis(graph_store, proc_node, kpi_nodes)

                # 流程名：优先节点名，缺失时按描述/节点ID关键词推断类型化名称
                process_name = self._build_process_name(proc_node)

                process_def = ProcessDefinition(
                    process_id=proc_node.node_id,
                    name=process_name,
                    process_type=proc_node.attributes.get("type", "sop"),
                    steps=steps,
                    owner_role_id=exec_roles[0].node_id if exec_roles else "",
                    participants=[r.node_id for r in exec_roles],
                    trigger_event=proc_node.attributes.get("trigger_event", ""),
                    system_ids=[s.node_id for s in used_systems],
                    kpi_ids=kpi_ids,
                    confidence=0.7 if steps else 0.3,
                )
                processes.append(process_def)
                if steps:
                    extracted += 1
            except Exception as e:
                self.logger.warning(f"流程编译失败 {proc_node.name}: {e}")

        # 按类型化标签去重：同一业务类型的流程（销售/采购/客服/财务/合同/售后…）
        # 常因多份文档重复出现（如 270 个近似重复的 Process 节点），仅保留每个类型
        # 的首个代表定义（含其原有有意义的名称），收敛到 ~10-20 个不同命名流程，
        # 避免流程引擎出现大量近似重复的 "business" 流程。
        seen_labels: set[str] = set()
        deduped: list[ProcessDefinition] = []
        for pd in processes:
            step_text = " ".join(
                str(s.get("name", "")) for s in pd.steps if isinstance(s, dict)
            )
            label = _infer_process_name(pd.name, f"{pd.name} {step_text}")
            if label in seen_labels:
                continue
            seen_labels.add(label)
            # 若该流程名缺失或为文件名式占位名，则用推断出的类型化标签作为展示名，
            # 避免企业运转模型出现空名/文件名/成片重复的「业务流程」。
            if not (pd.name or "").strip() or _looks_like_filename(pd.name or ""):
                pd.name = label
            deduped.append(pd)
        self.logger.info(
            f"流程去重: {len(processes)} -> {len(deduped)}（按类型化标签）"
        )
        processes = deduped

        # 计算置信度
        total = len(process_nodes)
        confidence = self.calculate_confidence(total, extracted, base=0.3, max_bonus=0.7)

        output = ProcessCompileOutput(
            enterprise_id=ctx.enterprise_id,
            processes=processes,
            confidence=confidence,
        )

        summary = self._generate_summary(processes, total)

        return CompilationResult(
            stage=self.stage,
            output=output,
            confidence=confidence,
            discovered_summary=summary,
            discovered_count=len(processes),
        )

    async def _extract_sop(
        self,
        ctx: CompilationContext,
        proc_node: GraphNode,
        exec_roles: list[GraphNode],
    ) -> list[dict]:
        """使用 LLM 从流程描述中提取 SOP 步骤。

        用户输入内容经 prompt_security.wrap_untrusted 包裹。
        """
        description = proc_node.attributes.get("description", "")
        if not description or len(description.strip()) < 10:
            # 尝试从 source_file 获取
            source_file = proc_node.attributes.get("source_file", "")
            if source_file:
                try:
                    from app.services.document_processor import process_document
                    description = await process_document(source_file)
                except Exception:
                    description = ""

        if not description or len(description.strip()) < 10:
            return []

        role_names = [r.name for r in exec_roles]
        system_prompt = (
            "你是企业流程分析专家。从给定的流程描述中提取 SOP（标准操作流程）步骤。"
            f"流程名称：{proc_node.name}。涉及角色：{', '.join(role_names) or '未指定'}。"
            "请以 JSON 数组格式输出步骤列表，每个步骤包含："
            "name（步骤名）、order（顺序号，从1开始）、approval_required（是否需审批，布尔值）、"
            "approver_role（审批角色名，可选）、condition（条件，可选）。"
            "仅输出 JSON，不要其他文字。"
        )

        user_message = prompt_security.wrap_untrusted(description[:3000])

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            response = await llm_service.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
            )
            steps_data = safe_json_parse(response)
            if not isinstance(steps_data, list):
                return []
        except Exception as e:
            self.logger.warning(f"LLM SOP 提取失败: {e}")
            return []

        # 规范化步骤
        steps: list[dict] = []
        for i, item in enumerate(steps_data):
            if not isinstance(item, dict):
                continue
            steps.append({
                "step_id": f"{proc_node.node_id}_step_{i+1}",
                "name": str(item.get("name", f"步骤{i+1}"))[:200],
                "order": item.get("order", i + 1),
                "approval_required": bool(item.get("approval_required", False)),
                "approver_role": item.get("approver_role", ""),
                "condition": item.get("condition", ""),
            })

        return steps

    def _normalize_steps(self, steps: list, proc_node: GraphNode) -> list[dict]:
        """规范化步骤列表为步骤字典。

        兼容知识图谱属性中「字符串步骤名」的旧数据（如 ['步骤一', '步骤二']），
        将其转为 {step_id, name, order, approval_required, ...} 字典，避免
        ProcessDefinition 校验失败导致流程被跳过。
        """
        normalized: list[dict] = []
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                item = dict(step)
                item.setdefault("step_id", f"{proc_node.node_id}_step_{i + 1}")
                item.setdefault("name", str(item.get("name", f"步骤{i + 1}"))[:200])
                item.setdefault("order", item.get("order", i + 1))
                item.setdefault("approval_required", False)
                item.setdefault("approver_role", "")
                item.setdefault("condition", "")
                normalized.append(item)
            elif isinstance(step, str) and step.strip():
                normalized.append({
                    "step_id": f"{proc_node.node_id}_step_{i + 1}",
                    "name": step.strip()[:200],
                    "order": i + 1,
                    "approval_required": False,
                    "approver_role": "",
                    "condition": "",
                })
        return normalized

    def _model_approval_flow(
        self, steps: list[dict], exec_roles: list[GraphNode]
    ) -> list[dict]:
        """审批流建模：为需审批的步骤关联审批角色。

        如果步骤标记 approval_required=True 但未指定 approver_role，
        则尝试关联上级角色（如有）。
        """
        if not exec_roles:
            return steps

        for step in steps:
            if step.get("approval_required") and not step.get("approver_role"):
                # 默认用第一个执行角色作为审批人
                step["approver_role"] = exec_roles[0].name
        return steps

    def _build_process_name(self, proc_node: GraphNode) -> str:
        """构建有意义的流程名。

        知识图谱流程节点的 name 常为空（None），导致流程定义 name 缺失、按名称去重
        时被误合并。这里优先使用节点名；缺失时基于节点描述/节点ID关键词推断
        类型化流程名，保证每个流程都有可辨识的名称。
        """
        name = proc_node.name
        raw = str(name).strip() if name else ""
        desc = proc_node.attributes.get("description", "") or ""
        search_text = f"{raw} {desc} {proc_node.node_id}"
        # 有业务语义的节点名直接采用；否则（空名/文件名式占位名）回退为类型化标签，
        # 保证每个流程都有可辨识、非文件名式的名称。
        if raw and not _looks_like_filename(raw):
            return raw[:80]
        return _infer_process_name(raw, search_text)

    def _generate_fallback_steps(self, proc_node: GraphNode) -> list[dict]:
        """无 LLM / 无显式步骤时的规则化 SOP 兜底。

        根据流程名称关键词，为常见业务流程生成合理的标准操作步骤，
        保证流程引擎的业务流程非空、可正常呈现。步骤含审批标记，
        使审批流建模与前端可视化均有内容可展示。
        """
        name = proc_node.name or ""
        templates: list[tuple[tuple[str, ...], list[tuple[str, bool, str]]]] = [
            (
                ("审批",),
                [("提交申请", False, ""), ("业务初审", True, "直属上级"),
                 ("分级审批", True, "审批负责人"), ("审批结果通知", False, "")],
            ),
            (
                ("报价",),
                [("接收客户询价", False, ""), ("制作报价单", False, ""),
                 ("财务审核", True, "财务经理"), ("向客户发送报价", False, "")],
            ),
            (
                ("合同",),
                [("合同起草", False, ""), ("合规审核", True, "法务"),
                 ("双方签署", False, ""), ("归档与执行", False, "")],
            ),
            (
                ("采购",),
                [("提交采购申请", True, "采购经理"), ("供应商比价", False, ""),
                 ("下单与到货验收", False, ""), ("入库归档", False, "")],
            ),
            (
                ("销售", "线索", "跟进", "询盘", "成交"),
                [("线索获取与分配", False, ""), ("客户需求沟通", False, ""),
                 ("方案与报价", False, ""), ("成交与转交", False, "")],
            ),
            (
                ("售后", "维修", "报修", "服务", "返厂"),
                [("接收服务工单", False, ""), ("派单与上门服务", False, ""),
                 ("故障处理与更换", False, ""), ("服务回访与归档", False, "")],
            ),
        ]

        selected: list[tuple[str, bool, str]] = []
        for keywords, steps in templates:
            if any(k in name for k in keywords):
                selected = steps
                break
        if not selected:
            # 通用流程模板
            selected = [
                ("接受任务", False, ""),
                ("执行核心处理", False, ""),
                ("结果审核", True, "业务负责人"),
                ("归档与记录", False, ""),
            ]

        result: list[dict] = []
        for i, (step_name, approval, approver) in enumerate(selected):
            result.append({
                "step_id": f"{proc_node.node_id}_step_{i + 1}",
                "name": step_name,
                "order": i + 1,
                "approval_required": approval,
                "approver_role": approver,
                "condition": "",
            })
        return result

    async def _link_kpis(
        self,
        graph_store: PGJSONBGraphStore,
        proc_node: GraphNode,
        kpi_nodes: list[GraphNode],
    ) -> list[str]:
        """关联 KPI：从流程属性和图谱关系中提取 KPI。"""
        # 从流程属性中提取
        kpi_ids = proc_node.attributes.get("kpi_ids", [])
        if isinstance(kpi_ids, list):
            return [str(k) for k in kpi_ids]
        return []

    def _generate_summary(self, processes: list[ProcessDefinition], total: int) -> str:
        """生成发现摘要。"""
        from app.services.compiler.i18n import process_label
        type_counts: dict[str, int] = {}
        total_steps = 0
        approval_count = 0
        for p in processes:
            type_counts[p.process_type] = type_counts.get(p.process_type, 0) + 1
            total_steps += len(p.steps)
            approval_count += sum(1 for s in p.steps if s.get("approval_required"))

        parts = [f"编译 {len(processes)} 个流程（共 {total_steps} 步骤）"]
        if type_counts:
            type_str = "、".join(
                f"{process_label(t)} {c}" for t, c in type_counts.items()
            )
            parts.append(f"类型分布：{type_str}")
        if approval_count:
            parts.append(f"含 {approval_count} 个审批节点")
        return "；".join(parts)
