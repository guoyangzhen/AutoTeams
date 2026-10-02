"""WT4 AI 优化顾问（PRD §5.4 + 重构方案 §7.6 阶段3）。

4 类建议（PRD §5.4）：
- knowledge（知识补充）：Agent 频繁失败于某类问题
- process（流程优化）：流程瓶颈/延迟
- capability（能力增强）：新业务场景出现
- organization（组织调整）：岗位负载不均

应用流程（MVP 仅"建议→确认→应用"）：
    Advisor 发现问题 → 生成建议 → 推送
      → 用户确认 → 一键应用（更新运行模型→重编译 Runtime→同步 Agent）
      → 用户拒绝 → 记录并降频推送

输入契约：
- 消费 WT1 CompilationGaps（spec.md §10.4）—— 缺失项建议的输入
- 消费 WT2 RuntimeQueryInterface（spec.md §10.5）—— 组织结构分析
- 消费 WT3 WorkforceRunData（spec.md §10.6）—— 运行指标分析

工程约束（spec §2.2/§2.6）：
- service 层写操作显式 await db.commit()
- LLM 调用前用户输入经 prompt_security.wrap_untrusted 包裹
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.collaboration import CollaborationEvent
from app.models.evolution import AdvisorSuggestion
from app.schemas.compiler import CompilationGaps
from app.services.llm_service import llm_service, ModelTier
from app.services.prompt_security import (
    wrap_untrusted,
    safe_json_extract,
    SYSTEM_PROMPT_GUARDRAIL,
)
from app.services.runtime import runtime_query
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


# 建议类型
TYPE_KNOWLEDGE = "knowledge"
TYPE_PROCESS = "process"
TYPE_CAPABILITY = "capability"
TYPE_ORGANIZATION = "organization"

_ADVISOR_PROMPT = """你是企业 AI 组织优化顾问。基于以下企业运行数据，生成优化建议。

企业运行时摘要：
{runtime_summary}

编译缺失项（CompilationGaps）：
{gaps_summary}

Agent 运行指标摘要：
{workforce_summary}

请生成 JSON 数组，每条建议含：
{{
    "type": "knowledge|process|capability|organization",
    "title": "建议标题（≤30字）",
    "description": "问题描述与建议（≤200字）",
    "impact": "预期效果（≤100字）"
}}

建议数量 3-8 条，按优先级排序。仅返回 JSON 数组，不要其他文字。
"""


class AdvisorService:
    """AI 优化顾问服务。

    Usage::

        svc = AdvisorService()
        items, total = await svc.list_suggestions(db, enterprise_id, limit=20, offset=0)
        result = await svc.generate_suggestions(db, enterprise_id)
        apply_result = await svc.apply_suggestion(db, suggestion_id)
    """

    # ========================================================
    # generate_suggestions
    # ========================================================

    async def generate_suggestions(
        self,
        db: AsyncSession,
        enterprise_id: str,
    ) -> list[dict[str, Any]]:
        """基于 Runtime + CompilationGaps + WorkforceRunData 生成 4 类建议。

        优先用 LLM 生成；LLM 不可用时回退到基于 gaps 的规则化建议。
        生成的建议持久化到 advisor_suggestions 表（状态 pending）。

        Returns:
            建议列表（dict 形式）
        """
        # 1. 读取三路输入（best-effort，缺一不阻断）
        gaps = await self._read_compilation_gaps(db, enterprise_id)
        runtime_summary = await self._build_runtime_summary(db, enterprise_id)
        workforce_summary = await self._build_workforce_summary(db, enterprise_id)

        # 2. 生成建议（LLM 优先，回退规则化）
        suggestions = await self._generate_via_llm(
            runtime_summary, gaps, workforce_summary
        )
        if not suggestions:
            suggestions = self._generate_rule_based(gaps, workforce_summary)

        # 3. 持久化
        created: list[dict[str, Any]] = []
        for s in suggestions:
            s_type = s.get("type", TYPE_KNOWLEDGE)
            if s_type not in (TYPE_KNOWLEDGE, TYPE_PROCESS, TYPE_CAPABILITY, TYPE_ORGANIZATION):
                s_type = TYPE_KNOWLEDGE
            record = AdvisorSuggestion(
                enterprise_id=enterprise_id,
                type=s_type,
                title=(s.get("title") or "优化建议")[:256],
                description=s.get("description") or "",
                impact=s.get("impact"),
                status="pending",
            )
            db.add(record)
            created.append(
                {
                    "type": s_type,
                    "title": record.title,
                    "description": record.description,
                    "impact": record.impact,
                }
            )

        await db.commit()
        logger.info(
            "Advisor 已生成 %d 条建议: enterprise=%s", len(created), enterprise_id
        )
        return created

    # ========================================================
    # apply_suggestion（一键应用）
    # ========================================================

    async def apply_suggestion(
        self, db: AsyncSession, suggestion_id: str
    ) -> dict[str, Any]:
        """一键应用建议：更新运行模型 → 触发重编译 → 同步 Agent（MVP 标记应用）。

        MVP 阶段：标记建议为 applied，记录受影响的 Agent（按 type 推导），
        触发 WT1 重编译（best-effort）。P1 阶段实现完整的运行模型更新。

        Returns:
            {"applied": True, "suggestion_id": str, "affected_agents": [str]}
        """
        record = await self._get_suggestion(db, suggestion_id)
        if record is None:
            raise ValueError(f"建议不存在: {suggestion_id}")
        if record.status == "applied":
            raise ValueError(f"建议已应用: {suggestion_id}")
        if record.status == "rejected":
            raise ValueError(f"建议已拒绝，无法应用: {suggestion_id}")

        # 推导受影响的 Agent（按建议类型）
        affected_agents = await self._derive_affected_agents(db, record)

        record.status = "applied"
        record.applied_at = datetime.now(timezone.utc)

        # 触发 WT1 重编译（best-effort）
        recompiled = await self._trigger_recompile(db, record.enterprise_id)

        await db.commit()
        logger.info(
            "建议已应用: suggestion=%s type=%s affected=%d recompiled=%s",
            suggestion_id, record.type, len(affected_agents), recompiled,
        )
        return {
            "applied": True,
            "suggestion_id": suggestion_id,
            "affected_agents": affected_agents,
            "message": f"建议已应用，受影响 Agent {len(affected_agents)} 个"
                       + ("，已触发重编译" if recompiled else "，重编译待首次编译后生效"),
        }

    # ========================================================
    # reject_suggestion
    # ========================================================

    async def reject_suggestion(
        self, db: AsyncSession, suggestion_id: str, reason: Optional[str] = None
    ) -> dict[str, Any]:
        """拒绝建议 → 记录并降频推送（PRD §5.4）。"""
        record = await self._get_suggestion(db, suggestion_id)
        if record is None:
            raise ValueError(f"建议不存在: {suggestion_id}")
        if record.status != "pending":
            raise ValueError(f"建议当前状态 {record.status}，无法拒绝")

        record.status = "rejected"
        # reason 暂存到 impact 字段尾部（不新增列，保持与迁移一致）
        if reason:
            existing = record.impact or ""
            record.impact = f"{existing}\n[拒绝原因] {reason}".strip()

        await db.commit()
        logger.info("建议已拒绝: suggestion=%s reason=%s", suggestion_id, reason)
        return {"rejected": True, "suggestion_id": suggestion_id}

    # ========================================================
    # list_suggestions
    # ========================================================

    async def list_suggestions(
        self,
        db: AsyncSession,
        enterprise_id: str,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AdvisorSuggestion], int]:
        """分页查询建议列表。"""
        conditions = [AdvisorSuggestion.enterprise_id == enterprise_id]
        if status:
            conditions.append(AdvisorSuggestion.status == status)

        count_result = await db.execute(
            select(func.count(AdvisorSuggestion.id)).where(*conditions)
        )
        total = int(count_result.scalar() or 0)

        result = await db.execute(
            select(AdvisorSuggestion)
            .where(*conditions)
            .order_by(AdvisorSuggestion.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(result.scalars().all())
        return items, total

    # ========================================================
    # 内部：读取 CompilationGaps（消费 WT1 §10.4 契约）
    # ========================================================

    async def _read_compilation_gaps(
        self, db: AsyncSession, enterprise_id: str
    ) -> Optional[CompilationGaps]:
        """读取最近一次编译的缺失项（spec.md §10.4）。

        复用 WT1 的 CompletenessCalculator + CompilationArtifact 读取路径。
        无编译任务时返回 None。
        """
        try:
            from app.models.compiler import CompilationArtifact, CompilationJob
            from app.services.compiler.base import CompilationContext
            from app.services.compiler.completeness import CompletenessCalculator

            job_result = await db.execute(
                select(CompilationJob)
                .where(CompilationJob.enterprise_id == enterprise_id)
                .order_by(CompilationJob.created_at.desc())
                .limit(1)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                return None

            artifact_result = await db.execute(
                select(CompilationArtifact)
                .where(CompilationArtifact.job_id == job.id)
                .order_by(CompilationArtifact.created_at.desc())
            )
            upstream: dict[str, Any] = {}
            seen: set[str] = set()
            for a in artifact_result.scalars():
                if a.stage not in seen:
                    seen.add(a.stage)
                    upstream[a.stage] = a.output

            ctx = CompilationContext(
                enterprise_id=enterprise_id, db=db, upstream=upstream
            )
            calculator = CompletenessCalculator(enterprise_id)
            completeness = calculator.calculate(ctx)
            return calculator.to_gaps_contract(completeness)
        except Exception as e:
            errors_total.labels(
                module=__name__, exception_type=type(e).__name__
            ).inc()
            logger.warning(
                "读取 CompilationGaps 失败（降级为无 gaps）: %s", e, exc_info=True
            )
            return None

    # ========================================================
    # 内部：构建运行时/Workforce 摘要（消费 WT2 §10.5 + WT3 §10.6）
    # ========================================================

    async def _build_runtime_summary(
        self, db: AsyncSession, enterprise_id: str
    ) -> str:
        """读取 WT2 Runtime 组织结构（spec.md §10.5）。"""
        try:
            organization = await runtime_query.get_organization(db, enterprise_id)
            if organization is None:
                return "（尚无激活 Runtime）"
            dept_count = len(organization.departments)
            return f"部门数 {dept_count}，汇报树节点 {len(organization.reporting_tree)}"
        except Exception as e:
            logger.warning("读取 Runtime 组织失败: %s", e, exc_info=True)
            return "（Runtime 读取失败）"

    async def _build_workforce_summary(
        self, db: AsyncSession, enterprise_id: str
    ) -> str:
        """读取 WT3 Workforce 运行数据（spec.md §10.6）。

        从 Agent 记录 + 协作事件构建运行指标摘要。
        """
        result = await db.execute(
            select(Agent).where(Agent.enterprise_id == enterprise_id)
        )
        agents = result.scalars().all()
        if not agents:
            return "（尚无 AI 员工）"

        # 协作事件统计
        event_count_result = await db.execute(
            select(func.count(CollaborationEvent.id)).where(
                CollaborationEvent.enterprise_id == enterprise_id
            )
        )
        event_count = int(event_count_result.scalar() or 0)

        positions = [a.position_id or a.name for a in agents]
        return (
            f"AI 员工 {len(agents)} 个，岗位：{', '.join(positions[:5])}，"
            f"协作事件 {event_count} 条"
        )

    # ========================================================
    # 内部：LLM 生成建议
    # ========================================================

    async def _generate_via_llm(
        self,
        runtime_summary: str,
        gaps: Optional[CompilationGaps],
        workforce_summary: str,
    ) -> list[dict[str, Any]]:
        """LLM 生成建议（用户/企业数据经 wrap_untrusted 包裹）。"""
        gaps_summary = "（无缺失项数据）"
        if gaps and gaps.gaps:
            gap_lines = [
                f"- [{g.gap_type}] {g.description}（影响完成度 +{g.impact_on_completeness}）"
                for g in gaps.gaps[:10]
            ]
            gaps_summary = f"整体完成度 {gaps.overall_completeness:.1f}\n" + "\n".join(gap_lines)

        # 企业运行数据视为不可信内容，包裹后再注入 prompt
        prompt = _ADVISOR_PROMPT.format(
            runtime_summary=wrap_untrusted(runtime_summary, "运行时摘要"),
            gaps_summary=wrap_untrusted(gaps_summary, "缺失项"),
            workforce_summary=wrap_untrusted(workforce_summary, "Workforce 摘要"),
        )

        try:
            response = await llm_service.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
                    {"role": "user", "content": prompt},
                ],
                tier=ModelTier.CHEAP,
            )
            # 优先尝试直接 json.loads（处理 JSON 数组 [{}] 和对象 {"suggestions": [...]}）
            parsed = None
            try:
                parsed = json.loads(response)
            except (json.JSONDecodeError, TypeError):
                pass
            # 回退到 safe_json_extract（从含噪声文本中提取首个 JSON 对象）
            if parsed is None:
                parsed = safe_json_extract(response)
            if isinstance(parsed, list):
                return [
                    {
                        "type": str(item.get("type", "knowledge")),
                        "title": str(item.get("title", "")),
                        "description": str(item.get("description", "")),
                        "impact": str(item.get("impact", "")) or None,
                    }
                    for item in parsed
                    if isinstance(item, dict)
                ]
            if isinstance(parsed, dict) and "suggestions" in parsed:
                return [
                    {
                        "type": str(item.get("type", "knowledge")),
                        "title": str(item.get("title", "")),
                        "description": str(item.get("description", "")),
                        "impact": str(item.get("impact", "")) or None,
                    }
                    for item in parsed["suggestions"]
                    if isinstance(item, dict)
                ]
        except Exception as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.warning("LLM 生成建议失败，回退规则化: %s", e, exc_info=True)
        return []

    # ========================================================
    # 内部：规则化建议（LLM 不可用时的回退）
    # ========================================================

    def _generate_rule_based(
        self,
        gaps: Optional[CompilationGaps],
        workforce_summary: str,
    ) -> list[dict[str, Any]]:
        """基于 CompilationGaps 的规则化建议（无 LLM 时回退）。"""
        suggestions: list[dict[str, Any]] = []
        if gaps and gaps.gaps:
            # gap_type → 建议类型映射
            type_map = {
                "knowledge": TYPE_KNOWLEDGE,
                "process": TYPE_PROCESS,
                "role": TYPE_ORGANIZATION,
                "data": TYPE_KNOWLEDGE,
                "tool": TYPE_CAPABILITY,
            }
            for gap in gaps.gaps[:8]:
                s_type = type_map.get(gap.gap_type, TYPE_KNOWLEDGE)
                suggestions.append(
                    {
                        "type": s_type,
                        "title": f"补全{gap.gap_type}缺失：{(gap.description or '')[:20]}",
                        "description": gap.description or "检测到缺失项",
                        "impact": gap.suggestion or f"补全后完成度可提升 {gap.impact_on_completeness}",
                    }
                )
        if not suggestions:
            # 无 gaps 数据时的兜底建议
            suggestions.append(
                {
                    "type": TYPE_ORGANIZATION,
                    "title": "完善企业运行模型",
                    "description": "当前企业运行模型数据不足，建议通过交互式访谈补全关键业务信息",
                    "impact": "提升运行模型完成度，使 AI 员工配置更精准",
                }
            )
        return suggestions

    # ========================================================
    # 内部：推导受影响 Agent + 触发重编译
    # ========================================================

    async def _derive_affected_agents(
        self, db: AsyncSession, suggestion: AdvisorSuggestion
    ) -> list[str]:
        """按建议类型推导受影响的 Agent ID 列表。"""
        result = await db.execute(
            select(Agent.id).where(Agent.enterprise_id == suggestion.enterprise_id)
        )
        agent_ids = [str(row[0]) for row in result.fetchall()]
        if not agent_ids:
            return []

        # MVP：knowledge/capability 影响全部 Agent；process/organization 影响生产态 Agent
        if suggestion.type in (TYPE_KNOWLEDGE, TYPE_CAPABILITY):
            return agent_ids
        # process/organization：仅影响 production 阶段 Agent
        prod_result = await db.execute(
            select(Agent.id).where(
                Agent.enterprise_id == suggestion.enterprise_id,
                Agent.lifecycle_stage == "production",
            )
        )
        prod_ids = [str(row[0]) for row in prod_result.fetchall()]
        return prod_ids or agent_ids

    async def _trigger_recompile(
        self, db: AsyncSession, enterprise_id: str
    ) -> bool:
        """应用建议后触发 WT1 重编译（best-effort，后台异步执行，不阻塞 HTTP 响应）。"""
        try:
            from app.models.compiler import CompilationJob
            from app.services.compiler.pipeline import CompilationPipeline

            job_result = await db.execute(
                select(CompilationJob)
                .where(CompilationJob.enterprise_id == enterprise_id)
                .order_by(CompilationJob.created_at.desc())
                .limit(1)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                return False

            job_id_val = job.id

            async def _bg_recompile():
                try:
                    from app.database import async_session_factory
                    async with async_session_factory() as bg_db:
                        bg_pipeline = CompilationPipeline(bg_db, enterprise_id)
                        await bg_pipeline.run_incremental(
                            job_id=job_id_val,
                            affected_stages=["information", "knowledge", "process", "capability", "runtime"],
                            interview_completion=0.0,
                        )
                except Exception as bg_e:
                    logger.warning("后台增量重编译执行异常: %s", bg_e)

            asyncio.create_task(_bg_recompile())
            return True
        except Exception as e:
            logger.warning("应用建议后重编译失败（不阻断应用）: %s", e, exc_info=True)
            return False
    # ========================================================
    # 内部辅助
    # ========================================================

    async def _get_suggestion(
        self, db: AsyncSession, suggestion_id: str
    ) -> Optional[AdvisorSuggestion]:
        result = await db.execute(
            select(AdvisorSuggestion).where(AdvisorSuggestion.id == suggestion_id)
        )
        return result.scalar_one_or_none()


# 模块级单例（无状态，可安全共享）
advisor_service = AdvisorService()
