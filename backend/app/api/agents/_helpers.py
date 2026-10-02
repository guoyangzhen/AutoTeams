"""agents 路由包共享助手 (3.3.3 拆分)。

本模块仅承载被多个子模块（crud/chat/knowledge）共用的 Agent 查找与校验函数，
不定义任何路由。子模块通过 ``from app.api.agents._helpers import ...`` 引用，
因此测试 monkeypatch 应针对具体子模块命名空间（如 ``app.api.agents.knowledge._get_agent_or_404``）
而非本模块——除非直接测试本模块函数。

3.3.6: 旧版双助手函数 ``_get_agent_or_404`` / ``_get_agent_or_404_for_watching``
已合并为单函数 + ``require_ready`` 参数；``_get_agent_or_404_for_watching`` 保留为
thin wrapper 仅为向后兼容（如测试 monkeypatch）。
"""
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import User
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)


async def _get_agent_or_404(
    db: AsyncSession,
    agent_id: str,
    current_user: User | None = None,
    require_ready: bool = True,
) -> Agent:
    """获取 Agent 并校验企业归属与状态（3.3.6 合并双助手函数）。

    P0-01: 如果传入 current_user，则同时校验企业归属。
    普通企业用户：必须匹配 enterprise_id；
    超级管理员（enterprise_id=None）：放行。

    Args:
        db: 数据库会话
        agent_id: Agent ID
        current_user: 当前用户（用于企业归属校验；None 表示跳过校验）
        require_ready: True 表示要求 status="ready"（默认），
            False 表示不校验状态（用于 watch/incremental-update 等需在
            processing 状态下操作的端点，替代旧的 _get_agent_or_404_for_watching）
    """
    query = select(Agent).where(Agent.id == agent_id)
    if current_user is not None:
        if current_user.enterprise_id is None and current_user.role != "admin":
            raise HTTPException(status_code=403, detail=ErrorCode.AGENT_ACCESS_DENIED)
        if current_user.enterprise_id is not None:
            query = query.where(Agent.enterprise_id == current_user.enterprise_id)
    result = await db.execute(query)
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if require_ready and agent.status != "ready":
        # BE-SEC-02: 不暴露内部状态值，使用统一错误码
        logger.warning(f"Agent 状态异常: agent_id={agent.id}, status={agent.status}")
        raise HTTPException(status_code=400, detail=ErrorCode.AGENT_STATUS_INVALID)
    return agent


async def _get_agent_or_404_for_watching(db: AsyncSession, agent_id: str, current_user: User) -> Agent:
    """校验 Agent 存在且归属当前用户的企业（不校验 status）。

    3.3.6: 旧版双助手函数之一，现已合并为 _get_agent_or_404 的 thin wrapper。
    保留此函数仅为向后兼容（如测试 monkeypatch）；新代码应直接调用
    _get_agent_or_404(..., require_ready=False)。

    普通企业用户：必须匹配 enterprise_id；
    超级管理员（enterprise_id=None）：放行（仅校验存在性）。
    """
    return await _get_agent_or_404(db, agent_id, current_user, require_ready=False)
