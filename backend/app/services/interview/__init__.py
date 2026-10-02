"""WT4 交互式企业访谈服务包（PRD §5.7）。

包含：
- question_bank: 7 大类问题库（销售/客服/采购/财务/人事/数据权限/KPI）
- interview_engine: 渐进式访谈引擎（start/next/submit/status）
"""
from app.services.interview.question_bank import QuestionBank, question_bank
from app.services.interview.interview_engine import InterviewEngine, interview_engine

__all__ = [
    "QuestionBank",
    "question_bank",
    "InterviewEngine",
    "interview_engine",
]
