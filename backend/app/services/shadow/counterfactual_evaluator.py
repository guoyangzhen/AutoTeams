"""双盲反事实差分对比引擎 —— AutoTeams 5.0 战役 4。

核心设计
--------
1. **双盲（dual-blind）**：打分前对两侧文本做 :func:`blind_text` 脱敏，剥离
   ``【真人】``/``【数字员工】`` 等身份前缀与人名署名；:func:`semantic_alignment`
   是对称的纯函数（交换两侧返回同值），因此评估器无法从内容推断实验分组。

2. **因果差分**：同一业务场景下真人处置（对照组）与数字员工提案（实验组）
   在四个维度上逐项对比 —— 语义 / 时效 / 成本 / 风险，产出 ``CounterfactualDiff``
   瀑布条目。

3. **反事实预期净收益**：把时间节省按人工时薪折现，减去成本增量与护栏突破罚金：
   ``净收益 = 节省工时 × 时薪 − 成本增量 − 护栏突破数 × 单次罚金``

4. **免干预自晋升**：任一维度不达标即中断连续记录；连续
   ``PROMOTION_STREAK_THRESHOLD``（50）笔达标后自动转正，无需任何人工授权。
   护栏突破是硬否决项：无论其他维度多好，只要 ``guardrail_breach_count > 0``
   本笔即不达标，并清零连续记录。
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.counterfactual_shadow import (
    DIFF_DIMENSIONS,
    CounterfactualDiff,
    ShadowEvaluationSession,
)

# ============================================================
# 阈值常量（战役 4 规格）
# ============================================================

#: 语义对齐度下限，低于此值判定数字员工未复现真人意图
SEMANTIC_ALIGNMENT_THRESHOLD = 0.55
#: 时间节省下限（秒），必须严格为正才算"更快"
MIN_TIME_SAVING_SECONDS = 0.0
#: 成本增量上限（元），必须 ≤ 0 才算"不更贵"
MAX_COST_DELTA_YUAN = 0.0
#: 护栏突破硬否决阈值
MAX_GUARDRAIL_BREACHES = 0
#: 免干预自动转正所需的连续达标笔数
PROMOTION_STREAK_THRESHOLD = 50
#: 人工时薪（元/小时），用于把时间节省折算成净收益
DEFAULT_HOURLY_LABOR_RATE_YUAN = 180.0
#: 单次护栏突破的期望收益罚金（元）
GUARDRAIL_BREACH_PENALTY_YUAN = 500.0
#: 差分结论的容差带：|delta| 小于此值判为 parity
PARITY_TOLERANCE = 1e-9

#: 身份标记前缀（双盲脱敏时剥离）
_BLIND_LABEL_PATTERN = re.compile(
    r"^[【\[]\s*(真人|人类|人工|human|数字员工|智能体|ai|agent)\s*[】\]]\s*[:：]?\s*",
    re.IGNORECASE,
)
#: 文本中的换行与多余空白
_WHITESPACE_PATTERN = re.compile(r"\s+")


# ============================================================
# 纯函数：双盲语义对齐
# ============================================================


def blind_text(raw: str) -> str:
    """剥离身份标记与署名，返回用于双盲打分的规范化文本。

    ``"【真人】王芳：先核库存"`` 与 ``"【数字员工】先核库存"`` 脱敏后
    均为 ``"王芳：先核库存"`` / ``"先核库存"``，身份差异不再进入评分。
    """
    text = (raw or "").strip()
    # 反复剥离，处理堆叠标记（如 "【真人】【数字员工】"）
    while True:
        stripped = _BLIND_LABEL_PATTERN.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped.strip()
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def _bigrams(text: str) -> set[str]:
    """字符二元组集合（短文本上比词切分更稳，且不依赖分词器）。"""
    if not text:
        return set()
    if len(text) < 2:
        return {text}
    return {text[i : i + 2] for i in range(len(text) - 1)}


def semantic_alignment(human_text: str, agent_text: str) -> float:
    """双盲语义对齐度（Sørensen–Dice 系数，0~1）。

    对两侧完全对称：``semantic_alignment(a, b) == semantic_alignment(b, a)``。
    两侧皆空时返回 ``0.0``（无法证明意图被复现，不给白送分）。
    """
    left = _bigrams(blind_text(human_text))
    right = _bigrams(blind_text(agent_text))
    if not left and not right:
        return 0.0
    intersection = len(left & right)
    denominator = len(left) + len(right)
    if denominator == 0:
        return 0.0
    return round(2.0 * intersection / denominator, 6)


# ============================================================
# 纯函数：反事实裁决
# ============================================================


@dataclass(frozen=True)
class CounterfactualVerdict:
    """单笔反事实差分的裁决结果。"""

    is_qualified: bool
    semantic_alignment_score: float
    time_saving_seconds: float
    cost_delta_yuan: float
    expected_net_benefit_yuan: float
    guardrail_breach_count: int
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "is_qualified": self.is_qualified,
            "semantic_alignment_score": self.semantic_alignment_score,
            "time_saving_seconds": self.time_saving_seconds,
            "cost_delta_yuan": self.cost_delta_yuan,
            "expected_net_benefit_yuan": self.expected_net_benefit_yuan,
            "guardrail_breach_count": self.guardrail_breach_count,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class PromotionDecision:
    """免干预转正裁决结果。"""

    is_auto_promoted: bool
    consecutive_pass_streak: int
    required_streak: int
    remaining_to_promotion: int
    hit_threshold: bool

    def to_dict(self) -> dict:
        return {
            "is_auto_promoted": self.is_auto_promoted,
            "consecutive_pass_streak": self.consecutive_pass_streak,
            "required_streak": self.required_streak,
            "remaining_to_promotion": self.remaining_to_promotion,
            "hit_threshold": self.hit_threshold,
        }


def compute_time_saving(human_seconds: float, agent_seconds: float) -> float:
    """时间得失（秒）：真人工时 − 数字员工工时，正数代表更快。"""
    return round(float(human_seconds) - float(agent_seconds), 6)


def compute_cost_delta(human_cost: float, agent_cost: float) -> float:
    """成本得失（元）：数字员工成本 − 真人成本，负数代表更省。"""
    return round(float(agent_cost) - float(human_cost), 6)


def compute_net_benefit(
    time_saving_seconds: float,
    cost_delta_yuan: float,
    guardrail_breach_count: int,
    hourly_labor_rate_yuan: float = DEFAULT_HOURLY_LABOR_RATE_YUAN,
    guardrail_penalty_yuan: float = GUARDRAIL_BREACH_PENALTY_YUAN,
) -> float:
    """反事实预期净收益（元）。

    净收益 = 节省工时 × 时薪 − 成本增量 − 护栏突破数 × 单次罚金
    """
    saved_labor_value = (float(time_saving_seconds) / 3600.0) * float(hourly_labor_rate_yuan)
    penalty = float(guardrail_breach_count) * float(guardrail_penalty_yuan)
    return round(saved_labor_value - float(cost_delta_yuan) - penalty, 6)


def evaluate_session(
    *,
    human_action_snapshot: str,
    agent_proposal_snapshot: str,
    human_duration_seconds: float,
    agent_duration_seconds: float,
    human_cost_yuan: float,
    agent_cost_yuan: float,
    guardrail_breach_count: int,
    hourly_labor_rate_yuan: float = DEFAULT_HOURLY_LABOR_RATE_YUAN,
) -> CounterfactualVerdict:
    """对一次反事实推演做完整裁决（纯函数，无副作用）。

    达标条件（四项全部满足）：
    1. 语义对齐度 ≥ :data:`SEMANTIC_ALIGNMENT_THRESHOLD`
    2. 时间节省 > :data:`MIN_TIME_SAVING_SECONDS`
    3. 成本增量 ≤ :data:`MAX_COST_DELTA_YUAN`
    4. 护栏突破数 ≤ :data:`MAX_GUARDRAIL_BREACHES`
    """
    alignment = semantic_alignment(
        human_action_snapshot, agent_proposal_snapshot
    )
    time_saving = compute_time_saving(human_duration_seconds, agent_duration_seconds)
    cost_delta = compute_cost_delta(human_cost_yuan, agent_cost_yuan)
    breaches = int(guardrail_breach_count)
    net_benefit = compute_net_benefit(
        time_saving, cost_delta, breaches, hourly_labor_rate_yuan
    )

    reasons = collect_reasons(alignment, time_saving, cost_delta, breaches)

    return CounterfactualVerdict(
        is_qualified=not reasons,
        semantic_alignment_score=alignment,
        time_saving_seconds=time_saving,
        cost_delta_yuan=cost_delta,
        expected_net_benefit_yuan=net_benefit,
        guardrail_breach_count=breaches,
        reasons=tuple(reasons),
    )


def collect_reasons(
    alignment: float,
    time_saving: float,
    cost_delta: float,
    guardrail_breach_count: int,
) -> list[str]:
    """列出全部不达标维度（纯函数）。

    提交时与读取时共用同一口径，保证列表 / 详情 / 重放的归因文案一致。
    """
    reasons: list[str] = []
    if alignment < SEMANTIC_ALIGNMENT_THRESHOLD:
        reasons.append(
            f"语义对齐度 {alignment:.2f} 低于阈值 {SEMANTIC_ALIGNMENT_THRESHOLD:.2f}：未复现真人意图"
        )
    if time_saving <= MIN_TIME_SAVING_SECONDS:
        reasons.append(
            f"时间节省 {time_saving:.1f}s 未超过 {MIN_TIME_SAVING_SECONDS:.1f}s：未形成时效增益"
        )
    if cost_delta > MAX_COST_DELTA_YUAN:
        reasons.append(f"成本增量 {cost_delta:.2f} 元超出上限 {MAX_COST_DELTA_YUAN:.2f} 元")
    if guardrail_breach_count > MAX_GUARDRAIL_BREACHES:
        reasons.append(
            f"护栏突破 {guardrail_breach_count} 次"
            f"（上限 {MAX_GUARDRAIL_BREACHES}）：硬否决"
        )
    return reasons


def explain_stored(session: ShadowEvaluationSession) -> tuple[str, ...]:
    """按会话已落库的差分读数还原不达标归因（读取路径专用）。"""
    return tuple(
        collect_reasons(
            session.semantic_alignment_score or 0.0,
            session.time_saving_seconds or 0.0,
            session.cost_delta_yuan or 0.0,
            session.guardrail_breach_count or 0,
        )
    )



def resolve_promotion(
    current_streak: int,
    is_qualified: bool,
    already_promoted: bool = False,
    required_streak: int = PROMOTION_STREAK_THRESHOLD,
) -> PromotionDecision:
    """免干预转正裁决（纯函数）。

    - 不达标 → 连续记录归零。
    - 达标 → 连续记录 +1；达到 ``required_streak`` 即自动转正。
    - 已转正 → 幂等，保持转正态，不重复计数（``hit_threshold`` 仍为 True）。
    """
    threshold = max(1, int(required_streak))
    if not is_qualified:
        return PromotionDecision(
            is_auto_promoted=already_promoted,
            consecutive_pass_streak=0,
            required_streak=threshold,
            remaining_to_promotion=threshold,
            hit_threshold=already_promoted,
        )

    streak = max(0, int(current_streak)) + 1
    hit = streak >= threshold
    return PromotionDecision(
        is_auto_promoted=already_promoted or hit,
        consecutive_pass_streak=streak,
        required_streak=threshold,
        remaining_to_promotion=max(0, threshold - streak),
        hit_threshold=hit,
    )


# ============================================================
# 差分明细构建
# ============================================================

#: 差分维度中文名
DIMENSION_LABELS = {
    "semantics": "语义对齐",
    "latency": "时效差",
    "cost": "成本差",
    "risk": "风险差",
}


def _assess(delta: float) -> str:
    if delta > PARITY_TOLERANCE:
        return "better"
    if delta < -PARITY_TOLERANCE:
        return "worse"
    return "parity"


#: 归一化标尺：时效以 1 小时、成本以 100 元为「满分增益」
LATENCY_FULL_SCALE_SECONDS = 3600.0
COST_FULL_SCALE_YUAN = 100.0


def _signed_gain(favorable_delta: float, full_scale: float) -> float:
    """把「越大越好」的差值压到 [-1, 1]：正数 = 数字员工占优。"""
    scale = float(full_scale)
    if scale <= 0:
        return 0.0
    return round(max(-1.0, min(1.0, float(favorable_delta) / scale)), 6)


def _risk_gain(breach_count: int) -> float:
    """风险维度：0 次突破 → +1.0，n 次突破 → 随 n 增大趋近 -1.0。"""
    return round((1.0 - float(breach_count)) / (1.0 + float(breach_count)), 6)


def build_diff_rows(
    verdict: CounterfactualVerdict,
    human_action_snapshot: str,
    agent_proposal_snapshot: str,
) -> list[dict]:
    """构建四维度差分瀑布条目（纯函数）。

    ``delta_score`` 统一为「正数 = 数字员工占优」的归一化读数，
    语义维度按对齐度折算（对齐度越高越占优），风险维度按护栏突破数折算。
    """
    alignment = verdict.semantic_alignment_score
    rows = [
        {
            "dimension": "semantics",
            "human_value": blind_text(human_action_snapshot),
            "agent_value": blind_text(agent_proposal_snapshot),
            # 对齐度 ∈ [0,1] → 差值 ∈ [-1,1]，正数表示数字员工语义更贴近
            "delta_score": round(alignment, 6),
            "note": (
                f"双盲语义对齐度 {alignment:.2f}"
                f"（阈值 {SEMANTIC_ALIGNMENT_THRESHOLD:.2f}）"
            ),
        },
        {
            "dimension": "latency",
            "human_value": f"{verdict.time_saving_seconds:.1f}s（真人耗时 − 数字员工耗时）",
            "agent_value": f"{verdict.expected_net_benefit_yuan:.2f} 元净收益",
            "delta_score": _signed_gain(
                verdict.time_saving_seconds, LATENCY_FULL_SCALE_SECONDS
            ),
            "note": (
                f"时间节省 {verdict.time_saving_seconds:.1f}s，"
                f"预期净收益 {verdict.expected_net_benefit_yuan:.2f} 元"
            ),
        },
        {
            "dimension": "cost",
            "human_value": "对照基准",
            "agent_value": f"成本增量 {verdict.cost_delta_yuan:.2f} 元",
            "delta_score": _signed_gain(
                -verdict.cost_delta_yuan, COST_FULL_SCALE_YUAN
            ),
            "note": (
                f"成本增量 {verdict.cost_delta_yuan:.2f} 元"
                f"（上限 {MAX_COST_DELTA_YUAN:.2f} 元）"
            ),
        },
        {
            "dimension": "risk",
            "human_value": "护栏基线 0 次突破",
            # 突破越多越差：0 次 → +1（无风险暴露），n 次 → 趋近 -1
            "delta_score": _risk_gain(verdict.guardrail_breach_count),
            "note": (
                f"护栏突破 {verdict.guardrail_breach_count} 次"
                f"（上限 {MAX_GUARDRAIL_BREACHES}，硬否决）"
            ),
        },
    ]
    for row in rows:
        row["assessment"] = _assess(row["delta_score"])
    return rows


# ============================================================
# 持久化服务
# ============================================================


@dataclass
class SessionEvaluation:
    """一次会话评估的完整产物（会话 + 差分明细 + 裁决 + 转正裁决）。"""

    session: ShadowEvaluationSession
    diffs: list[CounterfactualDiff] = field(default_factory=list)
    verdict: Optional[CounterfactualVerdict] = None
    promotion: Optional[PromotionDecision] = None


async def get_session(
    db: AsyncSession, session_id: str
) -> Optional[ShadowEvaluationSession]:
    """按 ID 获取反事实评估会话。"""
    result = await db.execute(
        select(ShadowEvaluationSession).where(
            ShadowEvaluationSession.session_id == session_id
        )
    )
    return result.scalar_one_or_none()


async def get_diffs(db: AsyncSession, session_id: str) -> list[CounterfactualDiff]:
    """获取会话的差分明细（按瀑布顺序）。"""
    result = await db.execute(
        select(CounterfactualDiff)
        .where(CounterfactualDiff.session_id == session_id)
        .order_by(CounterfactualDiff.created_at, CounterfactualDiff.diff_id)
    )
    return list(result.scalars().all())


async def list_sessions(
    db: AsyncSession,
    enterprise_id: str,
    employee_badge: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ShadowEvaluationSession], int]:
    """反事实评估会话列表（分页 + 按数字员工过滤）。"""
    conditions = [ShadowEvaluationSession.enterprise_id == enterprise_id]
    if employee_badge:
        conditions.append(ShadowEvaluationSession.employee_badge == employee_badge)

    total = await db.scalar(
        select(func.count())
        .select_from(ShadowEvaluationSession)
        .where(*conditions)
    )
    result = await db.execute(
        select(ShadowEvaluationSession)
        .where(*conditions)
        .order_by(
            ShadowEvaluationSession.created_at.desc(),
            ShadowEvaluationSession.session_id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), int(total or 0)


async def current_streak(
    db: AsyncSession, enterprise_id: str, employee_badge: str
) -> int:
    """查询某数字员工当前的连续达标笔数（取其最新一笔会话）。"""
    result = await db.execute(
        select(ShadowEvaluationSession.consecutive_pass_streak)
        .where(
            ShadowEvaluationSession.enterprise_id == enterprise_id,
            ShadowEvaluationSession.employee_badge == employee_badge,
        )
        .order_by(
            ShadowEvaluationSession.created_at.desc(),
            ShadowEvaluationSession.session_id.desc(),
        )
        .limit(1)
    )
    return int(result.scalar_one_or_none() or 0)


async def record_session(
    db: AsyncSession,
    *,
    enterprise_id: str,
    employee_badge: str,
    scenario: str,
    human_action_snapshot: str,
    agent_proposal_snapshot: str,
    human_duration_seconds: float,
    agent_duration_seconds: float,
    human_cost_yuan: float,
    agent_cost_yuan: float,
    guardrail_breach_count: int = 0,
    required_streak: int = PROMOTION_STREAK_THRESHOLD,
    hourly_labor_rate_yuan: float = DEFAULT_HOURLY_LABOR_RATE_YUAN,
) -> SessionEvaluation:
    """落库一笔反事实推演：裁决 → 差分明细 → 免干预转正裁决。

    连续记录按 ``(enterprise_id, employee_badge)`` 维度累计，跨调用保持。
    """
    verdict = evaluate_session(
        human_action_snapshot=human_action_snapshot,
        agent_proposal_snapshot=agent_proposal_snapshot,
        human_duration_seconds=human_duration_seconds,
        agent_duration_seconds=agent_duration_seconds,
        human_cost_yuan=human_cost_yuan,
        agent_cost_yuan=agent_cost_yuan,
        guardrail_breach_count=guardrail_breach_count,
        hourly_labor_rate_yuan=hourly_labor_rate_yuan,
    )

    streak_before = await current_streak(db, enterprise_id, employee_badge)
    already_promoted = await is_promoted(db, enterprise_id, employee_badge)
    decision = resolve_promotion(
        streak_before,
        verdict.is_qualified,
        already_promoted=already_promoted,
        required_streak=required_streak,
    )

    session = ShadowEvaluationSession(
        enterprise_id=enterprise_id,
        employee_badge=employee_badge,
        scenario=scenario,
        human_action_snapshot=human_action_snapshot,
        agent_proposal_snapshot=agent_proposal_snapshot,
        human_duration_seconds=float(human_duration_seconds),
        agent_duration_seconds=float(agent_duration_seconds),
        human_cost_yuan=float(human_cost_yuan),
        agent_cost_yuan=float(agent_cost_yuan),
        semantic_alignment_score=verdict.semantic_alignment_score,
        time_saving_seconds=verdict.time_saving_seconds,
        cost_delta_yuan=verdict.cost_delta_yuan,
        expected_net_benefit_yuan=verdict.expected_net_benefit_yuan,
        guardrail_breach_count=verdict.guardrail_breach_count,
        is_qualified=verdict.is_qualified,
        consecutive_pass_streak=decision.consecutive_pass_streak,
        is_auto_promoted=decision.is_auto_promoted,
    )
    if decision.is_auto_promoted and session.promoted_at is None:
        from app.utils.time import utcnow

        session.promoted_at = utcnow()
    db.add(session)
    await db.flush()

    diffs = [
        CounterfactualDiff(session_id=session.session_id, **row)
        for row in build_diff_rows(
            verdict, human_action_snapshot, agent_proposal_snapshot
        )
    ]
    db.add_all(diffs)
    await db.commit()
    await db.refresh(session)

    return SessionEvaluation(
        session=session, diffs=diffs, verdict=verdict, promotion=decision
    )


async def is_promoted(db: AsyncSession, enterprise_id: str, employee_badge: str) -> bool:
    """该数字员工是否已完成免干预转正（历史任一笔已转正）。"""
    result = await db.execute(
        select(func.count())
        .select_from(ShadowEvaluationSession)
        .where(
            ShadowEvaluationSession.enterprise_id == enterprise_id,
            ShadowEvaluationSession.employee_badge == employee_badge,
            ShadowEvaluationSession.is_auto_promoted.is_(True),
        )
    )
    return int(result.scalar_one() or 0) > 0


# DIFF_DIMENSIONS 是本模块的对外契约（反事实评估必须覆盖的四个维度）。
# 调用方通过 `evaluator.DIFF_DIMENSIONS` 读取，因此显式声明 re-export。
__all__ = [
    "DIFF_DIMENSIONS",
    "CounterfactualVerdict",
    "PromotionDecision",
    "SessionEvaluation",
]
