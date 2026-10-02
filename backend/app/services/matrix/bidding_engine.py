"""AutoTeams 4.0 3轮血条竞聘选拔算法引擎（BiddingEngine）。

实现量化方案陈述、反驳打分、生命值扣减与终局加权选拔逻辑。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class BidEvaluationResult(BaseModel):
    candidate_profile_id: str
    round_num: int
    statement: str
    score: float
    score_rationale: str
    remaining_hp: float
    is_eliminated: bool


class BiddingEngine:
    """竞聘选拔算法。"""

    @classmethod
    def evaluate_bid_round(
        cls,
        candidate_profile_id: str,
        round_num: int,
        statement: str,
        current_hp: float,
        score: float,
        score_rationale: Optional[str] = None,
    ) -> BidEvaluationResult:
        """评估一轮竞聘打分并结算 HP。

        公式：HP_r = HP_{r-1} - (10 - score) * 3
        """
        score = max(0.0, min(10.0, score))
        deduction = (10.0 - score) * 3.0
        remaining_hp = max(0.0, current_hp - deduction)
        is_eliminated = remaining_hp <= 0.0

        rationale = score_rationale or f"第 {round_num} 轮打分: {score}/10 分，扣减 HP: {deduction:.1f}"

        return BidEvaluationResult(
            candidate_profile_id=candidate_profile_id,
            round_num=round_num,
            statement=statement,
            score=score,
            score_rationale=rationale,
            remaining_hp=round(remaining_hp, 2),
            is_eliminated=is_eliminated,
        )

    @classmethod
    def adjudicate_winner(
        cls,
        candidates: List[Dict[str, Any]],
    ) -> Optional[str]:
        """终局裁决中标人。

        输入格式：[{candidate_profile_id, final_hp, performance_score}]
        计算加权得分：FinalScore = final_hp * 0.7 + performance_score * 0.3
        """
        if not candidates:
            return None

        survivors = [c for c in candidates if c.get("final_hp", 0) > 0]
        if not survivors:
            # 若全部阵亡，按最高 HP 幸存者破格选拔
            survivors = candidates

        best_score = -1.0
        winner_id = None

        for c in survivors:
            cid = c["candidate_profile_id"]
            hp = float(c.get("final_hp", 0))
            perf = float(c.get("performance_score", 100.0))
            composite = hp * 0.7 + perf * 0.3

            if composite > best_score:
                best_score = composite
                winner_id = cid

        logger.info(f"3轮竞聘终局裁决完毕，胜出员工: {winner_id}, 综合评分: {best_score:.2f}")
        return winner_id
