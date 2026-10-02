"""完成度评估框架（PRD §4.3 + §4.4）。

5 维加权计算（PRD §4.3）：
- 数据覆盖 30%（Information 级文件解析率）
- 流程覆盖 25%（Process 级流程提取率）
- 角色覆盖 20%（Capability 级岗位覆盖率）
- 置信度 15%（各级编译器置信度均值）
- 访谈完成 10%（用户访谈完成度，MVP 默认 0）

等级映射（PRD §4.3）：
- ≥ 0.8：runnable（可运行，建议部署）
- 0.6-0.8：basic（基本可用，建议补充指定缺失项）
- < 0.6：incomplete（不完整，列出关键缺失项）

产出 CompilationGaps（spec.md §10.4 契约），供 WT4 evolution advisor 消费。
"""
import logging

from app.services.compiler.base import CompilationContext
from app.schemas.compiler import (
    InformationCompileOutput,
    ProcessCompileOutput,
    CapabilityCompileOutput,
    CompletenessResult,
    CompilationGap,
    CompilationGaps,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class CompletenessCalculator:
    """完成度计算器。

    PRD §4.3 五维加权公式：
    completeness = data_coverage * 0.30 + process_coverage * 0.25
                 + role_coverage * 0.20 + avg_confidence * 0.15
                 + interview_completion * 0.10
    """

    # 维度权重（PRD §4.3）
    WEIGHTS = {
        "data_coverage": 0.30,
        "process_coverage": 0.25,
        "role_coverage": 0.20,
        "confidence": 0.15,
        "interview_completion": 0.10,
    }

    # 等级阈值（PRD §4.3：≥80% 可运行 / 60-79% 基本可用 / <60% 不完整）
    LEVEL_RUNNABLE = 0.8
    LEVEL_BASIC = 0.6

    def __init__(self, enterprise_id: str):
        self._enterprise_id = enterprise_id

    def calculate(
        self,
        ctx: CompilationContext,
        interview_completion: float = 0.0,
    ) -> CompletenessResult:
        """计算完成度。

        Args:
            ctx: 编译上下文（含各级产物）
            interview_completion: 访谈完成度 [0, 1]，MVP 默认 0

        Returns:
            CompletenessResult（含 5 维分数 + 总分 + 等级 + 缺失项）
        """
        # 1. 数据覆盖（Information 级）
        data_score = self._calc_data_coverage(ctx)
        # 2. 流程覆盖（Process 级）
        process_score = self._calc_process_coverage(ctx)
        # 3. 角色覆盖（Capability 级）
        role_score = self._calc_role_coverage(ctx)
        # 4. 置信度（各级均值）
        confidence_score = self._calc_avg_confidence(ctx)
        # 5. 访谈完成
        interview_score = max(0.0, min(1.0, interview_completion))

        dimensions = {
            "data_coverage": data_score,
            "process_coverage": process_score,
            "role_coverage": role_score,
            "confidence": confidence_score,
            "interview_completion": interview_score,
        }

        # 加权总分
        overall = (
            data_score * self.WEIGHTS["data_coverage"]
            + process_score * self.WEIGHTS["process_coverage"]
            + role_score * self.WEIGHTS["role_coverage"]
            + confidence_score * self.WEIGHTS["confidence"]
            + interview_score * self.WEIGHTS["interview_completion"]
        )

        # 等级映射
        level = self._map_level(overall)

        # 缺失项分析
        gaps = self._identify_gaps(dimensions, ctx)

        return CompletenessResult(
            overall=round(overall, 4),
            dimensions={k: round(v, 4) for k, v in dimensions.items()},
            level=level,
            gaps=gaps,
        )

    def to_gaps_contract(
        self, result: CompletenessResult
    ) -> CompilationGaps:
        """转换为 WT1→WT4 契约 CompilationGaps（spec.md §10.4）。"""
        return CompilationGaps(
            enterprise_id=self._enterprise_id,
            overall_completeness=result.overall,
            dimension_scores=result.dimensions,
            gaps=result.gaps,
            compiled_at=utcnow(),
        )

    def _calc_data_coverage(self, ctx: CompilationContext) -> float:
        """数据覆盖度：基于 Information 级文件解析率。"""
        info = ctx.upstream.get("information")
        if info is None:
            return 0.0
        if isinstance(info, InformationCompileOutput):
            total = info.total_files
            entries = len(info.entries)
        elif isinstance(info, dict):
            total = info.get("total_files", 0)
            entries = len(info.get("entries", []))
        else:
            return 0.0
        if total == 0:
            return 0.1  # 无文件时给基础分
        # 解析率 + 实体提取率综合
        parse_rate = min(entries / max(total, 1), 1.0) if total > 0 else 0
        return min(0.3 + 0.7 * parse_rate, 1.0)

    def _calc_process_coverage(self, ctx: CompilationContext) -> float:
        """流程覆盖度：基于 Process 级流程提取率。"""
        proc = ctx.upstream.get("process")
        if proc is None:
            return 0.0
        # 流程节点数
        process_count = 0
        if isinstance(proc, ProcessCompileOutput):
            process_count = len(proc.processes)
        elif isinstance(proc, dict):
            process_count = len(proc.get("processes", []))
        # 检查常见流程覆盖
        common_processes = ["采购", "销售", "财务", "客服", "审批"]
        covered = 0
        proc_names = []
        if isinstance(proc, ProcessCompileOutput):
            proc_names = [p.name for p in proc.processes]
        elif isinstance(proc, dict):
            proc_names = [p.get("name", "") for p in proc.get("processes", [])]
        for cp in common_processes:
            if any(cp in pn for pn in proc_names):
                covered += 1
        coverage = covered / len(common_processes)
        if process_count == 0:
            return 0.0
        return min(0.2 + 0.8 * coverage, 1.0)

    def _calc_role_coverage(self, ctx: CompilationContext) -> float:
        """角色覆盖度：基于 Capability 级岗位覆盖率。"""
        cap = ctx.upstream.get("capability")
        if cap is None:
            return 0.0
        positions = []
        if isinstance(cap, CapabilityCompileOutput):
            positions = cap.capability_matrix.positions
        elif isinstance(cap, dict):
            cm = cap.get("capability_matrix", cap)
            positions = cm.get("positions", [])
        if not positions:
            return 0.0
        # 检查关键岗位覆盖
        has_skills = sum(
            1 for p in positions
            if (p.get("required_skills") if isinstance(p, dict) else p.required_skills)
        )
        coverage = has_skills / len(positions) if positions else 0
        return min(0.3 + 0.7 * coverage, 1.0)

    def _calc_avg_confidence(self, ctx: CompilationContext) -> float:
        """置信度：各级编译器置信度均值。"""
        confidences: list[float] = []
        for stage in ["information", "knowledge", "process", "capability", "runtime"]:
            output = ctx.upstream.get(stage)
            if output is None:
                continue
            conf = 0.0
            if hasattr(output, "confidence"):
                conf = output.confidence
            elif isinstance(output, dict):
                conf = output.get("confidence", 0.0)
            confidences.append(conf)
        if not confidences:
            return 0.0
        return sum(confidences) / len(confidences)

    def _map_level(self, score: float) -> str:
        """等级映射。"""
        if score >= self.LEVEL_RUNNABLE:
            return "runnable"
        if score >= self.LEVEL_BASIC:
            return "basic"
        return "incomplete"

    def _identify_gaps(
        self, dimensions: dict[str, float], ctx: CompilationContext
    ) -> list[CompilationGap]:
        """识别缺失项（供 WT4 evolution advisor 消费）。"""
        gaps: list[CompilationGap] = []

        # 数据覆盖不足
        if dimensions["data_coverage"] < 0.5:
            gaps.append(CompilationGap(
                gap_type="data",
                description="企业文件数据覆盖不足，缺少关键业务文档",
                impact_on_completeness=(0.5 - dimensions["data_coverage"]) * self.WEIGHTS["data_coverage"],
                suggestion="建议上传组织架构、产品手册、SOP 文档等核心文件",
            ))

        # 流程覆盖不足
        if dimensions["process_coverage"] < 0.5:
            gaps.append(CompilationGap(
                gap_type="process",
                description="业务流程覆盖不完整，缺少采购/销售/财务等核心流程",
                impact_on_completeness=(0.5 - dimensions["process_coverage"]) * self.WEIGHTS["process_coverage"],
                suggestion="建议补充各业务线的 SOP 文档和审批流定义",
            ))

        # 角色覆盖不足
        if dimensions["role_coverage"] < 0.5:
            gaps.append(CompilationGap(
                gap_type="role",
                description="岗位能力定义不完整，缺少技能和权限信息",
                impact_on_completeness=(0.5 - dimensions["role_coverage"]) * self.WEIGHTS["role_coverage"],
                suggestion="建议通过员工访谈补充岗位职责和技能要求",
            ))

        # 置信度不足
        if dimensions["confidence"] < 0.5:
            gaps.append(CompilationGap(
                gap_type="knowledge",
                description="知识图谱置信度偏低，实体和关系抽取不确定",
                impact_on_completeness=(0.5 - dimensions["confidence"]) * self.WEIGHTS["confidence"],
                suggestion="建议补充更结构化的数据源以提升抽取准确率",
            ))

        # 访谈未完成
        if dimensions["interview_completion"] < 0.5:
            gaps.append(CompilationGap(
                gap_type="data",
                description="员工访谈尚未完成，缺少一线业务视角",
                impact_on_completeness=(0.5 - dimensions["interview_completion"]) * self.WEIGHTS["interview_completion"],
                suggestion="建议安排关键岗位员工访谈，补充实际业务流程细节",
            ))

        return gaps
