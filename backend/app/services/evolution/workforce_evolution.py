"""AutoTeams 4.0 组织自我进化闭环与影子模式转正引擎。

核心机制：
1. 影子模式（Shadow Mode）伴随考核与转正上岗裁决
2. LoopEngine 负向反馈反哺与 SOP 规程自动微调/补丁生成
3. 动态绩效与生命周期全链路轨迹归档

安全边界（AUD-07 / AUD-30）
---------------------------
* 转正门槛来自服务器政策（`graduation_policy`），请求体只能表达"更严格"；
  零样本、无评估结果、未关联 Agent 一律不予转正。
* SOP 规程演进按 `enterprise_id` 收敛，跨企业调用查不到记录；
  版本号走乐观锁，避免并发反馈互相覆盖。
"""
from __future__ import annotations

import copy
import logging
import uuid
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evolution import AdvisorSuggestion
from app.models.flow_card import FlowCardModel
from app.models.shadow import ShadowTask
from app.models.workforce import WorkforceLifecycle, WorkforceProfile
from app.services.evolution.graduation_policy import (
    VALID_EVAL_RESULTS,
    GraduationPolicy,
    load_graduation_policy,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class GraduationEvaluationResult(BaseModel):
    profile_id: str
    employee_badge: str
    display_name: str
    total_samples: int
    match_count: int
    mismatch_count: int
    pass_rate: float
    average_confidence: float
    graduated: bool
    reason: str
    policy_version: str = ""


def _tighten_samples(policy_min: int, requested: Optional[int]) -> int:
    """请求只能收紧门槛：请求更严格则采纳请求，不得低于服务器政策。"""
    if requested is None:
        return policy_min
    return max(policy_min, max(1, int(requested)))


def _tighten_threshold(policy_threshold: float, requested: Optional[float]) -> float:
    """请求只能收紧门槛：通过率阈值越高越严格。"""
    if requested is None:
        return policy_threshold
    return min(1.0, max(policy_threshold, float(requested)))


def _not_graduated(
    profile: WorkforceProfile,
    total: int,
    evaluated: int,
    match_count: int,
    mismatch_count: int,
    pass_rate: float,
    avg_conf: float,
    reason: str,
    policy: GraduationPolicy,
) -> GraduationEvaluationResult:
    """构造"未转正"结果；不修改任何数据库状态。"""
    return GraduationEvaluationResult(
        profile_id=profile.id,
        employee_badge=profile.employee_badge,
        display_name=profile.display_name,
        total_samples=total,
        match_count=match_count,
        mismatch_count=mismatch_count,
        pass_rate=pass_rate,
        average_confidence=avg_conf,
        graduated=False,
        reason=reason,
        policy_version=policy.policy_version,
    )


class WorkforceEvolutionEngine:
    """数字员工自我进化与影子模式上岗裁决。"""

    @classmethod
    async def evaluate_shadow_graduation(
        cls,
        db: AsyncSession,
        enterprise_id: str,
        profile_id: str,
        min_samples: Optional[int] = None,
        pass_rate_threshold: Optional[float] = None,
    ) -> GraduationEvaluationResult:
        """评估影子期数字员工是否满足转正要求。

        AUD-30：`min_samples` / `pass_rate_threshold` 只是调用方的请求，
        最终门槛一律取服务器政策，且请求只允许收紧不放宽。
        """
        policy = load_graduation_policy()
        effective_min_samples = _tighten_samples(policy.min_samples, min_samples)
        effective_threshold = _tighten_threshold(policy.pass_rate_threshold, pass_rate_threshold)

        # 1. 查询员工档案（企业边界在此收敛）
        p_stmt = select(WorkforceProfile).where(
            WorkforceProfile.id == profile_id,
            WorkforceProfile.enterprise_id == enterprise_id,
        )
        p_res = await db.execute(p_stmt)
        profile = p_res.scalar_one_or_none()
        if not profile:
            raise ValueError(f"数字员工档案不存在: {profile_id}")

        # 2. 未关联 Agent 的档案无法归属考核样本，一律不予转正。
        if not profile.agent_id:
            return _not_graduated(
                profile, 0, 0, 0, 0, 0.0, 0.0,
                "档案未关联 Agent，无法归属考核样本，不予转正",
                policy,
            )

        # 3. 只统计归属该 Agent 且已完成评估的影子任务
        task_stmt = select(ShadowTask).where(
            ShadowTask.enterprise_id == enterprise_id,
            ShadowTask.agent_id == profile.agent_id,
            ShadowTask.status.in_(["evaluating", "qualified", "autonomous"]),
        )
        t_res = await db.execute(task_stmt)
        tasks = list(t_res.scalars().all())

        total = len(tasks)
        evaluated_tasks = [t for t in tasks if t.eval_result in VALID_EVAL_RESULTS]
        evaluated = len(evaluated_tasks)
        if evaluated < effective_min_samples:
            return _not_graduated(
                profile,
                total,
                evaluated,
                0,
                0,
                0.0,
                0.0,
                f"考核样本量不足（有效评估样本 {evaluated}，至少需要 {effective_min_samples} 个）",
                policy,
            )

        match_count = sum(1 for t in evaluated_tasks if t.eval_result == "match")
        mismatch_count = evaluated - match_count
        pass_rate = match_count / evaluated

        confidences = [t.confidence for t in evaluated_tasks if t.confidence is not None]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        if pass_rate < effective_threshold:
            return _not_graduated(
                profile,
                total,
                evaluated,
                match_count,
                mismatch_count,
                round(pass_rate, 4),
                round(avg_conf, 2),
                (
                    f"考核未达标：当前通过率 {pass_rate*100:.1f}%"
                    f"（低于阈值 {effective_threshold*100:.0f}%），"
                    "需继续留在影子模式中进行反馈对齐学习"
                ),
                policy,
            )

        reason = (
            f"考核通过！综合拟合通过率 {pass_rate*100:.1f}%"
            f"（>= {effective_threshold*100:.0f}%），平均置信度 {avg_conf:.2f}，"
            f"依据{policy.describe()}正式准予上岗转正"
        )

        # 4. 推进状态为正式上岗
        profile.employment_status = "production"
        profile.performance_score = min(100.0, round(pass_rate * 100, 1))

        # 5. 沉淀生命周期轨迹（记录政策版本，便于追溯）
        lifecycle = WorkforceLifecycle(
            id=str(uuid.uuid4()),
            enterprise_id=enterprise_id,
            agent_id=profile.agent_id,
            stage="production",
            transition_reason=f"影子模式考核达标，通过率 {pass_rate*100:.1f}%",
            meta={
                "pass_rate": pass_rate,
                "samples": total,
                "evaluated_samples": evaluated,
                "avg_confidence": avg_conf,
                "policy_version": policy.policy_version,
                "policy": policy.describe(),
            },
        )
        db.add(lifecycle)

        # 6. 生成顾问优化建议
        suggestion = AdvisorSuggestion(
            id=str(uuid.uuid4()),
            enterprise_id=enterprise_id,
            type="organization",
            title=f"数字员工【{profile.display_name}】通过影子考核正式上岗",
            description=(
                f"工号 {profile.employee_badge} 已在全量业务场景开启自主服务，"
                "建议重点关注前两周的客户好评率与抽样审计结果。"
            ),
            impact="提升客户服务即时响应率，减少人工坐席初筛负担",
            status="applied",
            applied_at=utcnow(),
        )
        db.add(suggestion)

        await db.commit()
        await db.refresh(profile)
        return GraduationEvaluationResult(
            profile_id=profile.id,
            employee_badge=profile.employee_badge,
            display_name=profile.display_name,
            total_samples=total,
            match_count=match_count,
            mismatch_count=mismatch_count,
            pass_rate=round(pass_rate, 4),
            average_confidence=round(avg_conf, 2),
            graduated=True,
            reason=reason,
            policy_version=policy.policy_version,
        )

    @classmethod
    async def evolve_sop_guardrails(
        cls,
        db: AsyncSession,
        enterprise_id: str,
        flow_model_id: str,
        feedback_notes: List[str],
        expected_version: Optional[str] = None,
    ) -> Optional[FlowCardModel]:
        """根据 LoopEngine 负向反馈迭代生成 SOP 规程的新防护规则。

        AUD-07：企业归属进入查询条件，跨企业调用查不到记录；
        `expected_version` 提供乐观锁，避免并发反馈互相覆盖版本号。
        """
        stmt = select(FlowCardModel).where(
            FlowCardModel.id == flow_model_id,
            FlowCardModel.enterprise_id == enterprise_id,
        )
        res = await db.execute(stmt)
        flow_record = res.scalar_one_or_none()
        if not flow_record:
            return None

        old_ver = flow_record.version or "1.0.0"
        if expected_version is not None and expected_version != old_ver:
            raise ValueError(f"SOP 规程版本冲突：期望 {expected_version}，当前 {old_ver}")

        card_dict = copy.deepcopy(flow_record.flow_data or {})
        guardrails = card_dict.get("guardrails", {})

        # 根据反馈关键词自适应升级护栏
        notes_str = " ".join(feedback_notes)
        if any(w in notes_str for w in ["金额", "扣费", "支付", "隐私", "敏感"]):
            guardrails["high_risk_confirmation"] = True
        if any(w in notes_str for w in ["遗漏", "漏填", "没问"]):
            guardrails["adaptive_slot_filling"] = True

        card_dict["guardrails"] = guardrails
        # 增加版本号
        parts = old_ver.split(".")
        new_patch = int(parts[-1]) + 1 if len(parts) == 3 and parts[-1].isdigit() else 1
        new_ver = f"{parts[0]}.{parts[1]}.{new_patch}" if len(parts) == 3 else f"{old_ver}.1"

        flow_record.version = new_ver
        flow_record.flow_data = card_dict
        flag_modified(flow_record, "flow_data")

        await db.commit()
        await db.refresh(flow_record)
        logger.info(f"SOP 规程 {flow_record.flow_id} 已反哺进化至版本: {new_ver}")
        return flow_record
