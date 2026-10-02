"""生命周期管理器：MVP 3 阶段（PRD §5.6）。

MVP 阶段：
  Recruit → Training → Production

阶段转换条件：
- Recruit → Training: Agent 配置模板由 Runtime Compiler 生成
- Training → Production: 知识库/SOP/技能/工具配置全部注入完成

完整 7 阶段（P1 迭代，spec.md §10.6）：
  Recruit → Training → Production
  → Evaluation → Continuous Learning → Promotion → Retirement
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workforce import WorkforceLifecycle
from app.schemas.workforce import LifecycleStage, MVP_TRANSITIONS
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class LifecycleManager:
    """AI 数字员工生命周期管理器。

    用法：
        mgr = LifecycleManager()
        # 查询生命周期状态
        status = await mgr.get_lifecycle(db, agent_id)
        # 阶段转换
        result = await mgr.transition(db, agent_id, "training", reason="配置已注入")
    """

    async def get_lifecycle(
        self,
        db: AsyncSession,
        agent_id: str,
    ) -> dict:
        """查询 Agent 的生命周期状态 + 历史。

        Returns:
            {stage, stage_entered_at, history: [...]}
        """
        # 查询 Agent 当前阶段
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent 不存在: {agent_id}")

        # 查询生命周期历史
        stmt = (
            select(WorkforceLifecycle)
            .where(WorkforceLifecycle.agent_id == agent_id)
            .order_by(WorkforceLifecycle.stage_entered_at.asc())
        )
        history_records = (await db.execute(stmt)).scalars().all()

        # 当前阶段（取最新一条记录或 Agent.lifecycle_stage）
        current_stage = agent.lifecycle_stage or LifecycleStage.RECRUIT.value
        stage_entered_at = utcnow()
        if history_records:
            latest = history_records[-1]
            stage_entered_at = latest.stage_entered_at
        elif agent.created_at:
            stage_entered_at = agent.created_at

        history = [
            {
                "stage": r.stage,
                "stage_entered_at": r.stage_entered_at,
                "transition_reason": r.transition_reason,
            }
            for r in history_records
        ]

        return {
            "stage": current_stage,
            "stage_entered_at": stage_entered_at,
            "history": history,
        }

    async def transition(
        self,
        db: AsyncSession,
        agent_id: str,
        target_stage: str,
        reason: Optional[str] = None,
    ) -> dict:
        """阶段转换（含校验）。

        Returns:
            {new_stage, status}
        """
        # 查询 Agent
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent 不存在: {agent_id}")

        current_stage_str = agent.lifecycle_stage or LifecycleStage.RECRUIT.value

        # 解析目标阶段
        try:
            target = LifecycleStage(target_stage)
        except ValueError:
            raise ValueError(
                f"无效的生命周期阶段: {target_stage}。"
                f"有效值: {[s.value for s in LifecycleStage]}"
            ) from None

        try:
            current = LifecycleStage(current_stage_str)
        except ValueError:
            current = LifecycleStage.RECRUIT

        # 校验转换合法性
        self._validate_transition(current, target)

        # 校验转换条件
        self._check_transition_conditions(db, agent, current, target)

        # 执行转换
        agent.lifecycle_stage = target.value
        await db.flush()

        # 记录生命周期
        lifecycle = WorkforceLifecycle(
            agent_id=agent_id,
            enterprise_id=agent.enterprise_id,
            stage=target.value,
            stage_entered_at=utcnow(),
            transition_reason=reason or f"{current.value} → {target.value}",
        )
        db.add(lifecycle)
        await db.commit()

        logger.info(
            f"生命周期转换: agent_id={agent_id}, "
            f"{current.value} → {target.value}, reason={reason}"
        )

        return {
            "new_stage": target.value,
            "status": "success",
        }

    def _validate_transition(self, current: LifecycleStage, target: LifecycleStage) -> None:
        """校验阶段转换是否合法。"""
        # 同阶段不需要转换
        if current == target:
            raise ValueError(f"当前已在 {current.value} 阶段，无需转换")

        # MVP 阶段校验
        if current in MVP_TRANSITIONS:
            allowed = MVP_TRANSITIONS[current]
            if target not in allowed:
                raise ValueError(
                    f"MVP 阶段不支持 {current.value} → {target.value} 转换。"
                    f"允许的目标: {[s.value for s in allowed]}"
                )
        else:
            # 非 MVP 阶段（P1 阶段），MVP 中不允许转换
            raise ValueError(
                f"MVP 阶段不支持从 {current.value} 转换。"
                f"MVP 仅支持: recruit → training → production"
            )

    def _check_transition_conditions(
        self,
        db: AsyncSession,
        agent: Agent,
        current: LifecycleStage,
        target: LifecycleStage,
    ) -> None:
        """校验转换条件是否满足。

        Recruit → Training: Agent 配置模板存在（system_prompt 非空）
        Training → Production: 知识库/SOP/技能/工具配置注入完成
        """
        if current == LifecycleStage.RECRUIT and target == LifecycleStage.TRAINING:
            # Recruit → Training: 检查 system_prompt 是否已设置
            if not agent.system_prompt:
                raise ValueError(
                    "转换条件不满足：Agent 缺少 system_prompt 配置"
                )

        elif current == LifecycleStage.TRAINING and target == LifecycleStage.PRODUCTION:
            # Training → Production: 检查配置注入完成
            missing = []
            if not agent.system_prompt:
                missing.append("system_prompt")
            # 检查是否有技能（通过 Agent.skills 关系）
            # 注意：这里只做基本检查，实际技能注入由模板应用流程完成
            if agent.file_count == 0 and agent.knowledge_count == 0:
                missing.append("知识库（file_count 和 knowledge_count 均为 0）")
            if missing:
                raise ValueError(
                    f"转换条件不满足，缺少: {', '.join(missing)}"
                )

    async def list_workforce(
        self,
        db: AsyncSession,
        enterprise_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Agent], int]:
        """列出企业的 Workforce（分页）。

        排除离职/退休等非活跃阶段（retired/terminated/inactive/offboarded）的 Agent，
        避免在主列表中展示已离岗员工。
        """
        import sqlalchemy as sa
        # 排除非活跃阶段
        inactive_stages = ["retired", "terminated", "inactive", "offboarded"]
        stmt = (
            select(Agent)
            .where(
                Agent.enterprise_id == enterprise_id,
                sa.or_(
                    Agent.lifecycle_stage.is_(None),
                    ~Agent.lifecycle_stage.in_(inactive_stages),
                ),
            )
            .order_by(Agent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await db.execute(stmt)).scalars().all()

        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(Agent).where(
            Agent.enterprise_id == enterprise_id,
            sa.or_(
                Agent.lifecycle_stage.is_(None),
                ~Agent.lifecycle_stage.in_(inactive_stages),
            ),
        )
        total = (await db.execute(count_stmt)).scalar() or 0

        return list(items), total
