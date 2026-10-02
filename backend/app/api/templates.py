"""B6: 数字员工能力模板 API。

端点（无尾斜杠，符合 hard constraint）：
- GET /templates?role=customer_service  列出预置 + 企业私有模板
- GET /templates/skill-templates         列出预置 SkillTemplate
- POST /templates/{template_id}/apply    套用模板创建 Agent

鉴权：
- 全部需要登录（get_current_user）
- 企业隔离：apply 强制使用 current_user.enterprise_id，忽略请求体中的 enterprise_id
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.models.user import User
from app.services.template_service import (
    apply_agent_template,
    create_private_template,
    list_skill_templates,
    list_templates,
)
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.utils.metrics import errors_total
from app.utils.rate_limit import rate_limit_api
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["数字员工模板"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class TemplateApplyRequest(BaseModel):
    """套用模板创建 Agent 的请求体。"""
    folder_path: str = Field(..., description="知识源文件夹路径")
    name_override: Optional[str] = Field(None, description="覆盖模板默认的 Agent 名称")
    description_override: Optional[str] = Field(None, description="覆盖模板默认的 Agent 描述")


class TemplateCreateRequest(BaseModel):
    """企业私有模板「另存为」请求体。

    对齐《AutoTeams_优化方向与愿景蓝图.md》4.1.3 实现要点：
    「模板可被企业自定义后另存为企业私有模板」
    """
    role: str = Field(..., description="岗位角色：customer_service/sales/hr/ops")
    name: str = Field(..., min_length=1, max_length=255, description="模板名称")
    description: Optional[str] = Field(None, description="模板描述")
    system_prompt: str = Field(..., min_length=1, description="中文专业 system_prompt")
    skill_ids: list[str] = Field(default_factory=list, description="关联的 SkillTemplate code 列表")
    knowledge_structure: dict = Field(default_factory=dict, description="推荐的知识库结构")
    sample_dialogues: list[dict] = Field(default_factory=list, description="示例对话列表")


# ============================================================
# GET /templates
# ============================================================

@router.get("")
@rate_limit_api()
async def get_templates(
    request: Request,
    role: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出预置 + 企业私有模板。

    - 预置模板（is_preset=True）：所有企业可见
    - 企业私有模板（is_preset=False）：仅本企业可见
    - 可选 role 过滤：customer_service / sales / hr / ops
    """
    try:
        templates = await list_templates(
            db=db,
            enterprise_id=current_user.enterprise_id,
            role=role,
        )
        return success_response(
            [tpl.to_dict() if hasattr(tpl, "to_dict") else _serialize_template(tpl) for tpl in templates]
        )
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"列出模板失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


# ============================================================
# POST /templates  企业私有模板「另存为」
# ============================================================

@router.post("", status_code=201)
@rate_limit_api()
async def create_template(
    data: TemplateCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """企业将自定义配置另存为私有模板。

    - 仅写入当前企业可见的私有模板（is_preset=False, enterprise_id=current_user.enterprise_id）
    - role 必须在白名单内：customer_service / sales / hr / ops
    - system_prompt 不允许为空

    对齐《AutoTeams_优化方向与愿景蓝图.md》4.1.3 实现要点：
    「模板可被企业自定义后另存为企业私有模板」
    """
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    try:
        template = await create_private_template(
            db=db,
            enterprise_id=current_user.enterprise_id,
            role=data.role,
            name=data.name,
            description=data.description,
            system_prompt=data.system_prompt,
            skill_ids=data.skill_ids,
            knowledge_structure=data.knowledge_structure,
            sample_dialogues=data.sample_dialogues,
        )

        # P1/P2-INFRA: 记录审计日志（独立 session 避免与 db 状态冲突）
        try:
            async with async_session_factory() as audit_db:
                await log_audit(
                    audit_db, current_user, "create_template", "agent_template", template.id,
                    request=request,
                    details={
                        "role": data.role,
                        "name": data.name,
                        "enterprise_id": current_user.enterprise_id,
                    },
                )
                await audit_db.commit()
        except Exception as audit_err:
            logger.warning(f"私有模板创建审计日志写入失败（非致命）: {audit_err}", exc_info=True)

        return success_response(template.to_dict())
    except ValueError as e:
        logger.warning(f"私有模板创建参数错误: {e}")
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST) from e
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"私有模板创建失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.TEMPLATE_APPLY_FAILED) from e


# ============================================================
# GET /templates/skill-templates
# ============================================================

@router.get("/skill-templates")
@rate_limit_api()
async def get_skill_templates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出预置 SkillTemplate（供前端展示能力组合详情）。"""
    try:
        skill_tpls = await list_skill_templates(
            db=db,
            enterprise_id=current_user.enterprise_id,
        )
        return success_response(
            [st.to_dict() if hasattr(st, "to_dict") else _serialize_skill_template(st) for st in skill_tpls]
        )
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"列出 SkillTemplate 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


# ============================================================
# POST /templates/{template_id}/apply
# ============================================================

@router.post("/{template_id}/apply")
@rate_limit_api()
async def apply_template(
    template_id: str,
    data: TemplateApplyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """套用模板创建 Agent。

    流程：
    1. 校验当前用户已绑定企业
    2. 调 template_service.apply_agent_template 完成构建
    3. 返回 agent_id，前端可跳转至 /canvas/{agent_id} 查看画布

    注意：
    - 端点无尾斜杠，符合 hard constraint
    - enterprise_id 强制取自 current_user，忽略请求体
    """
    if not current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)

    if not data.folder_path or not data.folder_path.strip():
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)

    try:
        result = await apply_agent_template(
            db=db,
            db_session_factory=async_session_factory,
            template_id=template_id,
            enterprise_id=current_user.enterprise_id,
            folder_path=data.folder_path.strip(),
            name_override=data.name_override,
            description_override=data.description_override,
        )

        # P1/P2-INFRA: 记录模板应用审计日志（使用独立 session 避免与 db 状态冲突）
        try:
            async with async_session_factory() as audit_db:
                await log_audit(
                    audit_db, current_user, "apply_template", "agent", result["agent_id"],
                    request=request,
                    details={
                        "template_id": template_id,
                        "template_name": result.get("template_name"),
                        "skills_created": result.get("skills_created", 0),
                        "system_prompt_applied": result.get("system_prompt_applied", False),
                    },
                )
                await audit_db.commit()
        except Exception as audit_err:
            logger.warning(f"模板应用审计日志写入失败（非致命）: {audit_err}", exc_info=True)

        return success_response(result)
    except PermissionError as e:
        logger.warning(f"模板访问被拒: {e}")
        raise HTTPException(status_code=403, detail=ErrorCode.TEMPLATE_ACCESS_DENIED) from e
    except ValueError as e:
        logger.warning(f"模板应用参数错误: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.TEMPLATE_NOT_FOUND) from e
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"模板应用失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.TEMPLATE_APPLY_FAILED) from e


# ============================================================
# 工具方法
# ============================================================

def _serialize_template(tpl) -> dict:
    """兼容性兜底：若模型未实现 to_dict，手动序列化。"""
    return {
        "id": tpl.id,
        "role": tpl.role,
        "name": tpl.name,
        "description": tpl.description,
        "system_prompt": tpl.system_prompt,
        "skill_ids": tpl.skill_ids or [],
        "knowledge_structure": tpl.knowledge_structure or {},
        "sample_dialogues": tpl.sample_dialogues or [],
        "is_preset": tpl.is_preset,
        "enterprise_id": tpl.enterprise_id,
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
    }


def _serialize_skill_template(st) -> dict:
    """兼容性兜底：若 SkillTemplate 未实现 to_dict，手动序列化。"""
    return {
        "id": st.id,
        "code": st.code,
        "name": st.name,
        "skill_type": st.skill_type,
        "description": st.description,
        "config": st.config or {},
        "output_schema": st.output_schema or {},
        "is_preset": st.is_preset,
        "created_at": st.created_at.isoformat() if st.created_at else None,
        "updated_at": st.updated_at.isoformat() if st.updated_at else None,
    }
