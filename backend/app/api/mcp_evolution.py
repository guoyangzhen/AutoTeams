"""AutoTeams 4.0 MCP 工具服务与组织进化闭环 API 路由。

授权边界（AUD-01 / AUD-07 / AUD-30）
-----------------------------------
* `/mcp/call` 必须携带已认证用户的企业上下文，端侧工具据此拒绝越权设备；
  后端不再直接读取自身文件系统（见 `services/mcp/runner_bridge.py`）。
* `/evolution/sop/{id}/evolve` 与 `/evolution/workforce/{id}/graduate` 都先按
  `id + enterprise_id` 确认资源归属，再校验角色：跨企业请求与不存在的资源
  返回同一个 404，不会因为角色不同而泄露资源是否存在。
* 转正门槛来自服务器政策，请求体只能收紧。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.flow_card import FlowCardModel
from app.models.user import User
from app.models.workforce import WorkforceProfile
from app.services.mcp.client import MCPClient
from app.services.mcp.runner_bridge import MCPToolCallContext
from app.services.mcp.schema import (
    MCPServerConfig,
)
from app.services.evolution.workforce_evolution import WorkforceEvolutionEngine
from app.utils.security import get_current_user
from app.utils.tenant_scope import (
    ROLE_ADMIN,
    assert_role,
    load_tenant_scoped,
    require_enterprise_bound,
)
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AutoTeams MCP & Evolution"])

# 注册默认可用的 MCP 服务器清单
_DEFAULT_SERVERS: Dict[str, MCPServerConfig] = {
    "local_runner": MCPServerConfig(
        server_id="local_runner",
        name="端侧 Local Runner 桥接",
        transport="runner_bridge",
    ),
}


class CallToolRequest(BaseModel):
    server_id: str = "local_runner"
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class GraduateRequest(BaseModel):
    """转正评估请求。

    这里的两个阈值**只是请求**：最终门槛取服务器政策
    （`SHADOW_GRADUATION_*`），且只允许比政策更严格。取值边界同时用于
    拒绝明显异常的输入（AUD-30）。
    """

    min_samples: Optional[int] = Field(
        default=None,
        ge=1,
        le=10_000,
        description="可选：请求的最少有效评估样本量（不得低于服务器政策）",
    )
    pass_rate_threshold: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=1.0,
        description="可选：请求的及格通过率阈值（不得低于服务器政策）",
    )


class EvolveSOPRequest(BaseModel):
    feedback_notes: List[str] = Field(default_factory=list, description="负向反馈与调优要求")
    expected_version: Optional[str] = Field(
        default=None,
        description="可选：乐观锁。提交时规程的当前版本号，不一致则拒绝写入",
    )


@router.get("/mcp/servers")
async def list_mcp_servers(
    current_user: User = Depends(get_current_user),
):
    """查询当前环境已连接的 MCP 服务器列表。"""
    return success_response(data=[c.model_dump() for c in _DEFAULT_SERVERS.values()])


@router.get("/mcp/tools")
async def list_all_mcp_tools(
    current_user: User = Depends(get_current_user),
):
    """查询当前环境中可供数字员工调用的所有 MCP 工具契约。"""
    require_enterprise_bound(current_user)
    all_tools: List[Dict[str, Any]] = []
    for cfg in _DEFAULT_SERVERS.values():
        client = MCPClient(cfg)
        all_tools.extend(
            {**tool.model_dump(), "server_id": cfg.server_id}
            for tool in await client.list_tools()
        )
    return success_response(data=all_tools)


@router.post("/mcp/call")
async def call_mcp_tool(
    req: CallToolRequest,
    current_user: User = Depends(get_current_user),
):
    """试调用指定 MCP 服务器的工具。"""
    require_enterprise_bound(current_user)
    server_conf = _DEFAULT_SERVERS.get(req.server_id)
    if not server_conf:
        raise HTTPException(status_code=404, detail=f"未找到 MCP 服务器: {req.server_id}")

    context = MCPToolCallContext(
        enterprise_id=current_user.enterprise_id,
        user_id=current_user.id,
    )
    client = MCPClient(server_conf)
    result = await client.call_tool(req.tool_name, req.arguments, context)
    return success_response(data=result.model_dump())


@router.post("/evolution/workforce/{profile_id}/graduate")
async def evaluate_shadow_graduation(
    profile_id: str,
    req: GraduateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """触发影子模式数字员工转正评估（企业管理员决策）。"""
    require_enterprise_bound(current_user)
    profile = await load_tenant_scoped(
        db,
        WorkforceProfile,
        WorkforceProfile.id,
        profile_id,
        WorkforceProfile.enterprise_id,
        current_user,
        not_found_detail="数字员工档案不存在",
    )
    assert_role(current_user, ROLE_ADMIN)

    try:
        res = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
            db=db,
            enterprise_id=current_user.enterprise_id,
            profile_id=profile.id,
            min_samples=req.min_samples,
            pass_rate_threshold=req.pass_rate_threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return success_response(data=res.model_dump(), message="转正评估执行完成")


@router.post("/evolution/sop/{flow_model_id}/evolve")
async def evolve_sop_guardrails(
    flow_model_id: str,
    req: EvolveSOPRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """根据 LoopEngine 负向反馈迭代生成 SOP 规程新版本与护栏补丁。"""
    require_enterprise_bound(current_user)
    flow = await load_tenant_scoped(
        db,
        FlowCardModel,
        FlowCardModel.id,
        flow_model_id,
        FlowCardModel.enterprise_id,
        current_user,
        not_found_detail="未找到目标 SOP 规程模型",
    )
    assert_role(current_user, ROLE_ADMIN)

    try:
        updated = await WorkforceEvolutionEngine.evolve_sop_guardrails(
            db=db,
            enterprise_id=current_user.enterprise_id,
            flow_model_id=flow.id,
            feedback_notes=req.feedback_notes,
            expected_version=req.expected_version,
        )
    except ValueError as e:
        # 版本冲突（乐观锁）属于 409，不是 404。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到目标 SOP 规程模型")

    return success_response(
        data={
            "flow_id": updated.flow_id,
            "version": updated.version,
            "flow_data": updated.flow_data,
        },
        message=f"SOP 规程已成功进化并发布补丁版本 {updated.version}",
    )
