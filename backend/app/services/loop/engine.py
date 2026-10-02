"""Loop 核心优化循环（engine）。

从 ``app/services/loop_engine.py`` 拆分（原 343-599 行），并在此组装最终的
:class:`LoopEngine`：把五个职责单一的 mixin 组合成一个类。

闭环的「应用」阶段：
1. ``optimize_retrieval``  按用户反馈优化 RAG 检索结果
2. ``apply_optimization``  把落库的优化记录真正应用到 Agent 配置（先建版本快照，可回滚）
3. ``_apply_*``           按 ``OptimizationHistory.type`` 分发具体变更

组装顺序不影响行为（各 mixin 方法名互不重叠），但保持与阅读顺序一致便于排查。
"""
import json
import logging
from datetime import datetime, timezone

import chromadb.errors
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.optimization_history import OptimizationHistory
from app.services.agent_version_service import create_version_snapshot
from app.services.llm_service import llm_service
from app.services.prompt_security import (
    SYSTEM_PROMPT_GUARDRAIL,
    safe_json_extract,
    wrap_untrusted,
)
from app.services.vector_store import VectorStoreService
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode
from app.utils.metrics import errors_total

from app.services.loop.alerts import LoopAlertsMixin
from app.services.loop.analytics import LoopAnalyticsMixin
from app.services.loop.evaluation import LoopEvaluationMixin
from app.services.loop.insights import LoopInsightsMixin

logger = logging.getLogger(__name__)


class LoopCoreMixin:
    """核心优化循环（mixin，由本模块的 :class:`LoopEngine` 组装）。"""

    async def optimize_retrieval(
        self,
        db: AsyncSession,
        agent_id: str,
        query: str,
        feedback: str,
        user_id: str = None,
    ) -> dict:
        try:
            collection_name = f"agent_{agent_id}"
            # BE-PER-04: 使用异步工厂方法获取 VectorStoreService
            vector_store = await VectorStoreService.create(collection_name)

            results = await vector_store.search(query, n_results=5)

            # P0-08: 包裹不可信内容
            safe_query = wrap_untrusted(query, "用户查询")
            safe_feedback = wrap_untrusted(feedback, "用户反馈")
            safe_results = wrap_untrusted(
                json.dumps([r['content'][:100] for r in results], ensure_ascii=False),
                "当前检索结果"
            )

            optimization_prompt = f"""基于用户反馈优化检索结果。

用户查询：{safe_query}
用户反馈：{safe_feedback}

当前检索结果：
{safe_results}

请分析：
1. 当前检索结果的问题
2. 应该优先保留哪些结果
3. 是否需要调整查询关键词

以JSON格式返回：
{{
    "issue": "检索结果的问题",
    "keep_results": [0, 1],
    "refined_query": "优化后的查询",
    "reason": "原因"
}}
"""

            analysis = await llm_service.chat([
                {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
                {"role": "user", "content": optimization_prompt}
            ])

            # P0-08: 使用 safe_json_extract
            optimization = safe_json_extract(analysis)
            if optimization is None:
                optimization = {"issue": (analysis or "")[:200]}

            # 持久化到 OptimizationHistory 表
            history = OptimizationHistory(
                agent_id=agent_id,
                user_id=user_id,
                type="optimization",
                input_data={
                    "query": query,
                    "feedback": feedback,
                    "current_results": [r.get('content', '')[:100] for r in results],
                },
                output_data=optimization,
                applied=False,
            )
            db.add(history)
            await db.flush()
            await db.commit()

            return optimization

        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError, chromadb.errors.ChromaError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"优化检索失败: {e}", exc_info=True)
            # BE-SEC-02: 返回统一错误码，不暴露原始异常字符串
            return {"error": ErrorCode.INTERNAL_ERROR}
        except (OSError, TypeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"优化检索失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"优化检索失败: {e}") from e

    async def apply_optimization(
        self,
        db: AsyncSession,
        agent_id: str,
        optimization_id: str,
        user_id: str = None,
    ) -> dict:
        """O-08: 应用优化记录到 Agent 配置，打通 Loop 闭环。

        将原本「分析→存档→遗忘」的开环变为「分析→存档→应用」的闭环：
        1. 校验 OptimizationHistory 存在且未应用；
        2. 应用前创建 Agent 版本快照（捕获旧配置，便于回滚）；
        3. 按 type 应用具体变更：
           - feedback：追加注意事项到 system_prompt
           - gap：生成待补充文件清单建议写回 output_data
           - optimization：调整 RAG n_results/top_k
        4. 标记 applied=True、applied_at=now 并 commit。

        异常：
        - ValueError("optimization_not_found")：记录不存在或不属于该 Agent
        - ValueError("optimization_already_applied")：记录已被应用

        返回：{applied, optimization_id, version_snapshot_id, agent_version, summary}
        """
        # 1. 校验优化记录存在且归属该 Agent
        result = await db.execute(
            select(OptimizationHistory).where(
                OptimizationHistory.id == optimization_id,
                OptimizationHistory.agent_id == agent_id,
            )
        )
        history = result.scalar_one_or_none()
        if not history:
            raise ValueError("optimization_not_found")
        if history.applied:
            raise ValueError("optimization_already_applied")

        # 2. 查 Agent（与优化记录归属一致）
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise ValueError("optimization_not_found")

        # 3. 应用前创建版本快照（捕获旧配置，可回滚）；内部会递增 agent.version
        snapshot = await create_version_snapshot(
            db, agent, user_id,
            changelog=f"应用优化前快照（{history.type} #{(history.id or '')[:8]}）",
        )

        # 4. 按优化类型应用具体变更（在快照之后修改 agent 字段）
        summary = self._apply_by_type(agent, history)

        # 5. 标记已应用并提交
        history.applied = True
        history.applied_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(
            f"Loop 闭环：已应用优化 {history.type}#{(history.id or '')[:8]} "
            f"到 Agent {agent_id}，版本快照 {snapshot.id}（v{agent.version}）"
        )

        return {
            "applied": True,
            "optimization_id": history.id,
            "version_snapshot_id": snapshot.id,
            "agent_version": agent.version,
            "summary": summary,
        }

    def _apply_by_type(self, agent: Agent, history: OptimizationHistory) -> str:
        """根据优化类型分发应用逻辑，返回人类可读的摘要。"""
        opt_type = history.type
        output = history.output_data or {}

        if opt_type == "feedback":
            return self._apply_feedback(agent, output)
        elif opt_type == "gap":
            return self._apply_gap(agent, history, output)
        elif opt_type == "optimization":
            return self._apply_optimization(agent, output)
        else:
            logger.warning(f"未知优化类型 {opt_type}，仅创建快照未应用具体变更")
            return f"未知优化类型 {opt_type}，仅创建版本快照"

    def _apply_feedback(self, agent: Agent, output: dict) -> str:
        """feedback 类型：把不满意原因与改进建议作为注意事项追加到 system_prompt。"""
        issues = output.get("issues") or []
        if not issues:
            return "无反馈问题可应用"

        notes_lines: list[str] = []
        for issue in issues[:10]:
            if isinstance(issue, dict):
                reason = (issue.get("reason") or "").strip()
                improvement = (issue.get("improvement") or "").strip()
            else:
                reason = str(issue).strip()
                improvement = ""
            if not reason:
                continue
            line = f"- {reason}"
            if improvement:
                line += f"（改进：{improvement}）"
            notes_lines.append(line)

        if not notes_lines:
            return "无反馈问题可应用"

        notes_block = "【Loop 反馈优化注意事项】\n" + "\n".join(notes_lines)
        # 追加到末尾，保留原始 prompt（保证 startswith 原文）
        if agent.system_prompt:
            agent.system_prompt = agent.system_prompt.rstrip() + "\n\n" + notes_block
        else:
            agent.system_prompt = notes_block

        return f"已追加 {len(notes_lines)} 条反馈注意事项到系统提示"

    def _apply_gap(self, agent: Agent, history: OptimizationHistory, output: dict) -> str:
        """gap 类型：基于知识缺口与建议生成待补充文件清单，写回 output_data。"""
        gaps = output.get("gaps") or []
        suggestions = output.get("suggestions") or []

        recommended_files: list[dict] = []
        for gap in gaps[:10]:
            topic = str(gap)[:50]
            recommended_files.append({
                "topic": topic,
                "suggested_file": f"知识补充_{topic[:20]}.md",
            })
        for sug in suggestions[:5]:
            topic = str(sug)[:50]
            recommended_files.append({
                "topic": topic,
                "suggested_file": f"建议补充_{topic[:20]}.md",
            })

        applied_suggestion = {
            "recommended_files": recommended_files,
            "note": "请根据上述清单补充对应知识文件后重新扫描知识库",
        }

        # 重新赋值整个 output_data，确保 SQLAlchemy JSON 字段变更被检测
        new_output = dict(output)
        new_output["applied_suggestion"] = applied_suggestion
        history.output_data = new_output

        return f"已生成 {len(recommended_files)} 条待补充文件清单建议"

    def _apply_optimization(self, agent: Agent, output: dict) -> str:
        """optimization 类型：根据检索结果保留建议调整 RAG n_results/top_k。"""
        config = dict(agent.config or {})
        changed: list[str] = []

        keep_results = output.get("keep_results")
        if isinstance(keep_results, list) and keep_results:
            # 保留结果数作为新的 top_k/n_results 下限（至少 1）
            new_top_k = max(1, len(keep_results))
            config["top_k"] = new_top_k
            config["n_results"] = new_top_k
            changed.append(f"top_k/n_results={new_top_k}")

        refined_query = output.get("refined_query")
        if refined_query:
            refinements = list(config.get("query_refinements") or [])
            refinements.append(str(refined_query)[:200])
            config["query_refinements"] = refinements
            changed.append("refined_query")

        if changed:
            agent.config = config
            return f"已调整 RAG 配置：{', '.join(changed)}"
        return "无检索优化配置可调整"


class LoopEngine(
    LoopInsightsMixin,
    LoopCoreMixin,
    LoopEvaluationMixin,
    LoopAnalyticsMixin,
    LoopAlertsMixin,
):
    """反馈循环引擎（数据库持久化版）。

    由五个职责单一的 mixin 组合而成，各模块位于 ``app/services/loop/``：

    ==================  =====================================================
    mixin               职责
    ==================  =====================================================
    ``LoopInsightsMixin``   反馈 / 知识缺口的 LLM 分析（``insights.py``）
    ``LoopCoreMixin``       优化循环的应用阶段（``engine.py``）
    ``LoopEvaluationMixin`` RAG 质量评估与自动回滚（``evaluation.py``）
    ``LoopAnalyticsMixin``  监控统计与趋势（``analytics.py``）
    ``LoopAlertsMixin``     错误日志与派生告警（``alerts.py``）
    ==================  =====================================================
    """
