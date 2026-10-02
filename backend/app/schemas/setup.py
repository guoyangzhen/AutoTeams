from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# P0-DoS: 与 schemas/agent.py 保持一致的用户输入长度上限
from app.schemas.agent import MAX_USER_MESSAGE_LENGTH

# Setup 向导用户消息上限（向导对话通常比 chat 短，但允许粘贴需求文档）
MAX_SETUP_MESSAGE_LENGTH = MAX_USER_MESSAGE_LENGTH


class SetupStartRequest(BaseModel):
    folder_path: str
    enterprise_id: str


class SetupMessage(BaseModel):
    # P0-DoS: 限制向导消息长度，防止超大 payload 耗尽内存与 LLM token 预算
    content: str = Field(..., max_length=MAX_SETUP_MESSAGE_LENGTH)


class SetupResponse(BaseModel):
    session_id: str
    message: str
    suggestions: Optional[List[str]] = None


class ProcessingPlan(BaseModel):
    session_id: str
    files: List[Dict[str, Any]]
    estimated_time: int
    knowledge_structure: Dict[str, Any]


class PlanConfirm(BaseModel):
    confirmed: bool
    modifications: Optional[Dict[str, Any]] = None
    # auto_apply=True（默认）时直接把 plan 作为 initial_state 调 build_agent_via_graph，
    # 不再返回待确认方案；False 时仅返回方案供前端预览
    auto_apply: bool = True

    # P1-6.4: Setup 向导差异化参数显式字段。
    # 这些字段允许前端在确认 plan 时直接传入模型/索引策略/已选技能，
    # 替代通过 modifications 字典隐式传递，消除"配置幻觉"。
    # 若未提供，则优先回退到 modifications 中的同名键。
    model: Optional[str] = None
    index_strategy: Optional[str] = None
    selected_skills: Optional[List[str]] = None
