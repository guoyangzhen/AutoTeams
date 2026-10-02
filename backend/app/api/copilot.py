"""对话式 Agent 创建 Copilot API。

端点：POST /setup/copilot（无尾斜杠）
用户在 Home 对话框说一句话即可创建 Agent，跳过 7 步 Setup 向导。
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.copilot_service import copilot_service
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.audit import log_audit
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

# 复用 /setup 前缀，与 setup_router 共存（FastAPI 允许多 router 共享前缀）
router = APIRouter(prefix="/setup", tags=["对话式 Agent 创建"])


class CopilotRequest(BaseModel):
    """对话式创建请求。"""
    message: str
    # 可选会话 ID，用于将来串联多轮对话；当前单轮即可完成创建
    session_id: Optional[str] = None


@router.post("/copilot")
@rate_limit_api()
async def setup_copilot(
    request: Request,
    data: CopilotRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """对话式 Agent 创建入口。

    用户说："用 D:\\公司文档 文件夹做一个 HR 答疑助手"
    自动：解析意图 → 扫描文件夹 → 生成方案 → 调用 build_agent_via_graph(require_approval=False)
    返回：{status:"building", thread_id, agent_name, redirect_url:"/canvas/{thread_id}"}
    """
    # 强制使用当前用户的企业 ID
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.SETUP_ENTERPRISE_REQUIRED)
    if not data.message or not data.message.strip():
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)

    try:
        result = await copilot_service.create_agent_from_message(
            data.message, current_user, db
        )

        # 记录审计日志（build 已在 graph 内用独立 session 提交，此处仅写审计）
        if result.get("agent_id"):
            await log_audit(
                db, current_user, "copilot_create_agent", "agent", result["agent_id"],
                request=request,
                details={
                    "thread_id": result.get("thread_id"),
                    "agent_name": result.get("agent_name"),
                },
            )
            await db.commit()

        return success_response(result)
    except FileNotFoundError as e:
        # S10: 不向客户端泄漏异常详情，仅记录服务端日志
        logger.warning(f"Copilot 文件夹不存在: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.FOLDER_NOT_FOUND) from e
    except PermissionError as e:
        # S10: 不向客户端泄漏异常详情，仅记录服务端日志
        logger.warning(f"Copilot 文件夹无访问权限: {e}")
        raise HTTPException(status_code=403, detail=ErrorCode.FOLDER_ACCESS_DENIED) from e
    except ValueError as e:
        # S10: 路径不合法 / 未解析出路径 / 未绑定企业 — 不泄漏具体原因
        logger.warning(f"Copilot 参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.FOLDER_PATH_INVALID) from e
    except (
        SQLAlchemyError, httpx.HTTPError, RuntimeError, OSError, TypeError,
    ) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"对话式创建 Agent 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e
