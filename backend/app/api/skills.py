import logging
import os
import re
import shutil
import socket
import tempfile
import uuid
import zipfile
import ipaddress
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.models.agent import Agent
from app.schemas.skill import (
    SkillResponse,
    SkillCreate,
    SkillUpdate,
    SkillExecuteRequest,
    SkillExecutionResponse,
    SkillGenerateRequest,
    SkillImportRequest,
    SkillReviewRequest,
)
from app.services.path_security import (
    PathSecurityError,
    sanitize_path_for_response,
    validate_upload_path,
)
from app.services.skill_executor import skill_executor
from app.services.skill_generator import skill_generator
from app.services.skill_screener import SkillScreener
from app.utils.security import get_current_user
from app.utils.rbac import require_admin
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_upload, rate_limit_api, rate_limit_admin
from app.utils.audit import log_audit
from app.utils.metrics import errors_total
from app.utils.upload_validation import (
    FileUploadError,
    check_magic_bytes,
    detect_file_type,
    max_size_for_file_type,
    save_upload_to_disk,
    scan_file_for_viruses,
    validate_content_type,
)
from app.utils.error_codes import ErrorCode

import httpx
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["技能"])

async def _verify_skill_access(
    db: AsyncSession, skill_id: str, current_user: User
) -> Skill:
    """P0-01: 校验技能存在且归属当前用户的企业（通过 agent 关联）。

    通过 JOIN Agent 表实现多租户隔离：
    - 普通企业用户：必须匹配 enterprise_id
    - 超级管理员（enterprise_id=None）：放行（仅校验存在性）
    """
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)
    query = select(Skill).join(Agent, Skill.agent_id == Agent.id).where(
        Skill.id == skill_id
    )
    if current_user.enterprise_id is not None:
        query = query.where(Agent.enterprise_id == current_user.enterprise_id)
    result = await db.execute(query)
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=ErrorCode.SKILL_NOT_FOUND)
    return skill


@router.get("")
@rate_limit_api()
async def list_skills(
    request: Request,
    agent_id: str | None = None,
    limit: int = Query(200, ge=1, le=500, description="每页数量（1-500）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01: 通过 JOIN Agent 过滤企业归属
    if current_user.enterprise_id is None and current_user.role != "admin":
        return success_response([])
    query = select(Skill).join(Agent, Skill.agent_id == Agent.id)
    if current_user.enterprise_id is not None:
        query = query.where(Agent.enterprise_id == current_user.enterprise_id)
    if agent_id:
        query = query.where(Skill.agent_id == agent_id)

    result = await db.execute(query.order_by(Skill.created_at.desc()).limit(limit).offset(offset))
    skills = result.scalars().all()
    return success_response([SkillResponse.model_validate(s).model_dump() for s in skills])


@router.post("", status_code=201)
@rate_limit_api()
async def create_skill(
    data: SkillCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建新技能。

    P2-1: 支持手动创建技能，不再仅依赖 Agent 构建时自动生成。
    需要校验 agent_id 归属（当前用户的企业）。
    """
    # 校验 agent 存在且属于当前用户的企业
    agent_result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id is None and current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.SKILL_ACCESS_DENIED)
    if current_user.enterprise_id is not None and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.SKILL_ACCESS_DENIED)

    skill = Skill(
        agent_id=data.agent_id,
        name=data.name,
        description=data.description,
        skill_type=data.skill_type,
        input_type=data.input_type,
        output_type=data.output_type,
        config=data.config or {},
        source=data.source or "manual",
        status=data.status or "approved",
        permissions=data.permissions or [],
    )
    db.add(skill)
    await db.flush()
    await db.refresh(skill)
    # P1/P2-INFRA: 记录技能创建审计日志
    await log_audit(
        db, current_user, "create", "skill", str(skill.id),
        request=request,
        details={"agent_id": data.agent_id, "skill_type": data.skill_type, "source": skill.source},
    )
    await db.commit()
    await db.refresh(skill)

    return success_response(SkillResponse.model_validate(skill).model_dump())


@router.get("/{skill_id}")
@rate_limit_api()
async def get_skill(
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # P0-01: 校验企业归属
    skill = await _verify_skill_access(db, skill_id, current_user)
    return success_response(SkillResponse.model_validate(skill).model_dump())


@router.put("/{skill_id}")
@rate_limit_admin()
async def update_skill(
    skill_id: str,
    data: SkillUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新技能配置（需要管理员权限）。"""
    # P0-01: 校验企业归属
    skill = await _verify_skill_access(db, skill_id, current_user)

    # 只更新非 None 字段
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(skill, field, value)

    await db.flush()
    # P1/P2-INFRA: 记录技能更新审计日志
    await log_audit(
        db, current_user, "update", "skill", skill_id,
        request=request,
        details={"updated_fields": list(update_data.keys())},
    )
    await db.commit()
    await db.refresh(skill)

    return success_response(SkillResponse.model_validate(skill).model_dump())


@router.delete("/{skill_id}")
@rate_limit_api()
async def delete_skill(
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除技能及其执行记录。"""
    # P0-01: 校验企业归属
    skill = await _verify_skill_access(db, skill_id, current_user)

    # 先删除关联的执行记录
    exec_result = await db.execute(
        select(SkillExecution).where(SkillExecution.skill_id == skill_id)
    )
    for exec_rec in exec_result.scalars().all():
        await db.delete(exec_rec)

    await db.delete(skill)
    await db.flush()
    # P1/P2-INFRA: 记录技能删除审计日志
    await log_audit(
        db, current_user, "delete", "skill", skill_id,
        request=request,
        details={"agent_id": skill.agent_id},
    )
    await db.commit()

    return success_response({"message": "技能已删除"})


@router.post("/{skill_id}/execute")
@rate_limit_api()
async def execute_skill(
    skill_id: str,
    data: SkillExecuteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """执行技能，自动持久化执行记录。

    P2-1: 每次执行都写入 SkillExecution 表（成功/失败均记录）。
    P0-05: 校验 skill → agent 归属当前用户的企业。
    """
    # P0-05: 执行前校验企业归属（与 P0-01 同一 helper）
    await _verify_skill_access(db, skill_id, current_user)

    try:
        result = await skill_executor.execute_skill(
            db=db,
            skill_id=skill_id,
            input_data=data.input_data,
            user_id=current_user.id,
        )
        # P1/P2-INFRA: 记录技能执行审计日志
        await log_audit(
            db, current_user, "execute", "skill", skill_id,
            request=request,
            details={"success": result.get("success", True) if isinstance(result, dict) else True},
        )
        await db.commit()
        return success_response(result)
    except PermissionError as e:
        # P0-05-B: 服务层二次校验失败 → 403
        logger.warning(f"执行技能权限不足: {e}")
        raise HTTPException(status_code=403, detail=ErrorCode.SKILL_EXECUTION_FORBIDDEN) from e
    except ValueError as e:
        logger.warning(f"执行技能参数错误: {e}")
        raise HTTPException(status_code=404, detail=ErrorCode.SKILL_NOT_FOUND_OR_INVALID) from e
    except (httpx.HTTPError, RuntimeError, OSError, TypeError, SQLAlchemyError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"执行技能失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.INTERNAL_ERROR) from e


@router.get("/{skill_id}/executions")
@rate_limit_api()
async def list_skill_executions(
    skill_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="每页数量（1-100）"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询技能的执行历史记录。

    P2-1: 支持分页查询，按创建时间倒序。
    P0-01: 校验 skill 归属当前用户的企业。
    """
    # P0-01: 校验企业归属
    await _verify_skill_access(db, skill_id, current_user)

    result = await db.execute(
        select(SkillExecution)
        .where(SkillExecution.skill_id == skill_id)
        .order_by(SkillExecution.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    executions = result.scalars().all()
    return success_response([
        SkillExecutionResponse.model_validate(e).model_dump() for e in executions
    ])


@router.post("/generate")
@rate_limit_api()
async def generate_skills(
    request: Request,
    data: SkillGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-SKILL: 基于对话历史与知识库，AI 自动生成 Skill。

    生成的 Skill 默认 source="generated"、status="pending"，
    需要经过安全筛查与管理员审批后方能执行。

    B6: 低风险自动放行
    - 条件：generated + static_screening.passed + review_run.passed + 无 permissions + skill_type 低风险
    - 满足条件时 status="approved"，review_result.auto_approved 记录原因与时间戳
    - 低风险 skill_type: search / lookup / summary（只读检索与摘要，无副作用）
    """
    agent_result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.SKILL_ACCESS_DENIED)

    # B6: 低风险自动放行的 skill_type 白名单
    # - search/lookup/summary：语义上为只读检索与摘要，无副作用
    _LOW_RISK_SKILL_TYPES = {"search", "lookup", "summary"}

    try:
        skills = await skill_generator.generate_for_agent(
            db=db, agent=agent, max_skills=data.max_skills
        )
        await db.flush()

        # P1-SKILL: 对生成的技能执行静态筛查 + 沙箱试运行
        screener = SkillScreener()
        for skill in skills:
            screening = screener.screen(skill)
            review_result = {"static_screening": screening.to_dict()}
            if not screening.passed:
                skill.status = "rejected"
                review_result["review_run"] = {"passed": False, "reason": "静态筛查未通过"}
            else:
                review_run = await screener.review_run(
                    db=db, skill=skill, user_id=current_user.id,
                    sample_input=screening.details.get("sample_input"),
                )
                review_result["review_run"] = review_run
                if not review_run["passed"]:
                    skill.status = "rejected"
                else:
                    # B6: 低风险自动放行判定
                    # 条件：generated + 双重筛查通过 + 无 permissions + skill_type 在白名单
                    if (
                        skill.source == "generated"
                        and not (skill.permissions or [])
                        and skill.skill_type in _LOW_RISK_SKILL_TYPES
                    ):
                        skill.status = "approved"
                        review_result["auto_approved"] = {
                            "reason": "low_risk_auto_approve",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
            # 沙箱试运行可能内部 commit，统一赋值确保变更被追踪
            skill.review_result = review_result
            await db.flush()

        await db.commit()
        for skill in skills:
            await db.refresh(skill)

        await log_audit(
            db, current_user, "generate", "skill", None,
            request=request,
            details={
                "agent_id": agent.id,
                "count": len(skills),
                "auto_approved": sum(
                    1 for s in skills
                    if (s.review_result or {}).get("auto_approved", {}).get("reason") == "low_risk_auto_approve"
                ),
            },
        )
        await db.commit()

        return success_response({
            "generated": len(skills),
            "skills": [SkillResponse.model_validate(s).model_dump() for s in skills],
        })
    except (httpx.HTTPError, RuntimeError, OSError, ValueError, TypeError) as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"生成 Skill 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.SKILL_GENERATION_FAILED) from e


@router.post("/import", status_code=201)
@rate_limit_api()
async def import_skill(
    request: Request,
    data: SkillImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """P1-SKILL: 导入外部 Skill 包。

    导入后统一进入 pending 状态，并立即执行静态安全筛查。
    只有通过筛查的技能才会进入待审批队列。
    """
    agent_result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=ErrorCode.AGENT_NOT_FOUND)
    if current_user.enterprise_id and agent.enterprise_id != current_user.enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.SKILL_ACCESS_DENIED)

    package = data.package or {}
    required = {"name", "skill_type"}
    missing = required - set(package.keys())
    if missing:
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.SKILL_IMPORT_INVALID,
        )

    skill = Skill(
        agent_id=data.agent_id,
        name=str(package.get("name", "")).strip(),
        description=package.get("description"),
        skill_type=package.get("skill_type"),
        input_type=package.get("input_type", "text"),
        output_type=package.get("output_type", "text"),
        config=package.get("config", {}),
        permissions=package.get("permissions", []),
        source="imported",
        status="pending",
    )
    db.add(skill)
    await db.flush()
    await db.refresh(skill)

    # 静态安全筛查 + 沙箱试运行
    screener = SkillScreener()
    screening = screener.screen(skill)
    review_result = {
        "static_screening": screening.to_dict(),
    }

    if not screening.passed:
        skill.status = "rejected"
        review_result["review_run"] = {"passed": False, "reason": "静态筛查未通过"}
    else:
        review_run = await screener.review_run(
            db=db, skill=skill, user_id=current_user.id,
            sample_input=screening.details.get("sample_input"),
        )
        review_result["review_run"] = review_run
        if not review_run["passed"]:
            skill.status = "rejected"

    # 在沙箱试运行可能内部 commit 后统一赋值，确保变更被 SQLAlchemy 追踪
    skill.review_result = review_result
    await db.flush()
    await db.refresh(skill)

    await log_audit(
        db, current_user, "import", "skill", str(skill.id),
        request=request,
        details={
            "agent_id": data.agent_id,
            "screening_passed": screening.passed,
            "review_run_passed": review_result["review_run"].get("passed"),
        },
    )
    await db.commit()
    await db.refresh(skill)

    return success_response(SkillResponse.model_validate(skill).model_dump())


@router.post("/{skill_id}/approve")
@rate_limit_admin()
async def approve_skill(
    skill_id: str,
    request: Request,
    data: SkillReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-SKILL: 管理员审批通过待审 Skill。"""
    skill = await _verify_skill_access(db, skill_id, current_user)
    if skill.status not in ("pending", "rejected", "draft"):
        raise HTTPException(
            status_code=400, detail=ErrorCode.SKILL_REVIEW_INVALID_STATE
        )

    skill.status = "approved"
    current_review = skill.review_result or {}
    current_review["approval"] = {
        "approved_by": current_user.id,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "reason": data.reason,
    }
    skill.review_result = current_review

    await db.flush()
    await log_audit(
        db, current_user, "approve", "skill", skill_id,
        request=request, details={"reason": data.reason},
    )
    await db.commit()
    await db.refresh(skill)
    return success_response(SkillResponse.model_validate(skill).model_dump())


@router.post("/{skill_id}/reject")
@rate_limit_admin()
async def reject_skill(
    skill_id: str,
    request: Request,
    data: SkillReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-SKILL: 管理员拒绝待审 Skill。"""
    skill = await _verify_skill_access(db, skill_id, current_user)
    if skill.status == "approved":
        raise HTTPException(
            status_code=400, detail=ErrorCode.SKILL_REVIEW_ALREADY_COMPLETE
        )

    skill.status = "rejected"
    current_review = skill.review_result or {}
    current_review["rejection"] = {
        "rejected_by": current_user.id,
        "rejected_at": datetime.now(timezone.utc).isoformat(),
        "reason": data.reason,
    }
    skill.review_result = current_review

    await db.flush()
    await log_audit(
        db, current_user, "reject", "skill", skill_id,
        request=request, details={"reason": data.reason},
    )
    await db.commit()
    await db.refresh(skill)
    return success_response(SkillResponse.model_validate(skill).model_dump())


@router.post("/{skill_id}/screen")
@rate_limit_admin()
async def screen_skill(
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """P1-SKILL: 对已有 Skill 重新执行安全筛查（沙箱试运行待扩展）。"""
    skill = await _verify_skill_access(db, skill_id, current_user)
    screener = SkillScreener()
    result = screener.screen(skill)
    skill.review_result = result.to_dict()
    await db.flush()
    await log_audit(
        db, current_user, "screen", "skill", skill_id,
        request=request, details=result.to_dict(),
    )
    await db.commit()
    await db.refresh(skill)
    return success_response({
        "skill_id": skill_id,
        "screening": result.to_dict(),
        "skill": SkillResponse.model_validate(skill).model_dump(),
    })


# ---------------------------------------------------------------------------
# M11: 技能包 URL 拉取辅助函数
# ---------------------------------------------------------------------------

def _filename_from_disposition(disposition: str) -> Optional[str]:
    """从 Content-Disposition 头解析文件名。

    支持 filename*=UTF-8''<url-encoded> 与 filename="<...>" 两种格式。
    """
    # filename*=UTF-8''<url-encoded-value>
    m = re.search(r"filename\*\s*=\s*([^;]+)", disposition, re.IGNORECASE)
    if m:
        val = m.group(1).strip().strip('"')
        if "'" in val:
            # charset'language'value
            parts = val.split("'", 2)
            if len(parts) == 3:
                return unquote(parts[2])
            return unquote(val)
        return unquote(val)
    # filename="..."
    m = re.search(r'filename\s*=\s*"([^"]+)"', disposition, re.IGNORECASE)
    if m:
        return m.group(1)
    # filename=... (unquoted)
    m = re.search(r"filename\s*=\s*([^;]+)", disposition, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return None


def _safe_remove_tmp(path: str) -> None:
    """安全删除临时文件，忽略不存在的文件。"""
    try:
        os.remove(path)
    except OSError:
        pass


def _extract_zip_to_upload_root(
    zip_path: str, user_id: str
) -> list[dict]:
    """解压 zip 到 UPLOAD_ROOT/<user_id>/，返回文件信息列表。

    - 跳过目录、__MACOSX/、*.DS_Store
    - 路径遍历检查：拒绝以 / 或 \\ 开头或包含 .. 的条目
    - 每个条目使用 UUID 文件名保存，扩展名保留
    """
    files: list[dict] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entry_name = info.filename
            # 跳过 macOS 元数据
            if "__MACOSX/" in entry_name or entry_name.endswith(".DS_Store"):
                continue
            # 路径遍历检查：拒绝绝对路径与包含 .. 的条目
            norm = entry_name.replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/"):
                raise PathSecurityError(f"zip 条目路径非法: {entry_name}")
            # 仅取 basename，防止嵌套目录写入
            base_name = os.path.basename(entry_name)
            if not base_name or base_name in (".", ".."):
                continue
            _, entry_ext = os.path.splitext(base_name)
            safe_name = f"{uuid.uuid4().hex}{entry_ext.lower()}"
            safe_path = validate_upload_path(user_id, safe_name)
            # 解压单个条目到目标路径
            with zf.open(info, "r") as src, open(safe_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            rel = sanitize_path_for_response(str(safe_path))
            file_size = os.path.getsize(safe_path)
            files.append({
                "original_name": base_name,
                "file_path": rel,
                "file_size": file_size,
                "file_type": entry_ext.lstrip(".").lower() if entry_ext else "file",
            })
    return files


async def _handle_multipart_upload(
    file: UploadFile,
    request: Request,
    db: AsyncSession,
    current_user: User,
):
    """M11: 处理 multipart/form-data 文件上传（原有逻辑，保持向后兼容）。"""
    filename = file.filename or "unknown"

    try:
        file_type = detect_file_type(filename)
        validate_content_type(filename, file.content_type, file_type)

        max_size = max_size_for_file_type(file_type)
        # 使用完全 UUID 文件名存储，防止路径遍历与文件名注入
        _, ext = os.path.splitext(filename)
        safe_filename = f"{uuid.uuid4().hex}{ext.lower()}"
        safe_path = validate_upload_path(
            str(current_user.id),
            safe_filename,
        )

        file_size, first_chunk = await save_upload_to_disk(file, safe_path, max_size)
        check_magic_bytes(first_chunk, file_type, filename)

        # T21: ClamAV 病毒扫描（CLAMD_HOST 为空时自动跳过）
        await scan_file_for_viruses(str(safe_path))

    except FileUploadError as e:
        # 统一错误码映射：技能上传复用文件类错误码，保持语义一致
        detail = ErrorCode.SKILL_FILE_TYPE_NOT_ALLOWED if e.error_code == ErrorCode.FILE_TYPE_NOT_ALLOWED else (
            ErrorCode.SKILL_FILE_SIZE_EXCEEDED if e.error_code == ErrorCode.FILE_SIZE_EXCEEDED else e.error_code
        )
        raise HTTPException(status_code=e.status_code, detail=detail) from e
    except PathSecurityError:
        raise HTTPException(status_code=400, detail=ErrorCode.FILE_PATH_INVALID) from None
    except OSError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"保存技能上传文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=ErrorCode.FILE_UPLOAD_FAILED) from e
    finally:
        await file.close()

    # 在响应中脱敏为相对路径，避免暴露服务器绝对路径
    rel_path = sanitize_path_for_response(str(safe_path))

    logger.info(f"用户 {current_user.id} 上传技能文件: {filename} -> {safe_path}")

    # P1/P2-INFRA: 记录技能文件上传审计日志
    await log_audit(
        db, current_user, "upload", "skill_file", rel_path,
        request=request,
        details={"original_name": filename, "file_size": file_size, "file_type": file_type},
    )
    await db.commit()

    return success_response({
        "original_name": filename,
        "file_path": rel_path,
        "file_size": file_size,
        # 保持与原有 API 兼容：返回扩展名字符串（如 "txt"）
        "file_type": ext.lstrip(".").lower() if ext else file_type,
    })


def _map_file_upload_error(error_code: str) -> str:
    """将底层 FileUploadError 错误码映射为技能上传语义错误码。"""
    if error_code == ErrorCode.FILE_TYPE_NOT_ALLOWED:
        return ErrorCode.SKILL_FILE_TYPE_NOT_ALLOWED
    if error_code == ErrorCode.FILE_SIZE_EXCEEDED:
        return ErrorCode.SKILL_FILE_SIZE_EXCEEDED
    return error_code


async def _handle_url_fetch(
    package_url: str,
    request: Request,
    db: AsyncSession,
    current_user: User,
):
    """M11: 从 HTTPS URL 拉取技能包并保存到 UPLOAD_ROOT。

    安全策略：
      1. 仅允许 https scheme
      2. SSRF 防护：DNS 解析后校验 IP 不在内网/保留段
      3. 流式下载，Content-Length 预检 + 边写边校验大小上限
      4. 30s 总超时、10s 连接超时、禁用重定向
      5. 复用 detect_file_type / validate_content_type / check_magic_bytes /
         scan_file_for_viruses（zip 文件单独处理 magic bytes）
      6. zip 文件解压后逐条目保存（跳过目录与 macOS 元数据，校验路径遍历）
    """
    # 1. URL 解析与 scheme 校验
    parsed = urlparse(package_url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        logger.warning(f"技能包 URL scheme 非法或缺少 host: {package_url}")
        raise HTTPException(status_code=400, detail=ErrorCode.SKILL_PACKAGE_URL_INVALID)

    host = parsed.hostname

    # 2. 从 URL 路径提取文件名（Content-Disposition 在响应头阶段补全）
    url_path_filename = os.path.basename(parsed.path) if parsed.path else ""
    if url_path_filename:
        url_path_filename = unquote(url_path_filename)
    if not url_path_filename or url_path_filename in (".", ".."):
        url_path_filename = "package.bin"

    # 3. detect_file_type（zip 单独处理，因 .zip 不在 FILE_TYPE_MAP 中）
    is_zip_by_url = url_path_filename.lower().endswith(".zip")
    if is_zip_by_url:
        file_type = "zip"
        max_size = max_size_for_file_type("document")
    else:
        try:
            file_type = detect_file_type(url_path_filename)
        except FileUploadError:
            raise HTTPException(
                status_code=400, detail=ErrorCode.SKILL_PACKAGE_URL_INVALID
            ) from None
        max_size = max_size_for_file_type(file_type)
    _, url_ext = os.path.splitext(url_path_filename)

    # 4. SSRF 防护：DNS 解析后校验每个返回 IP
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        logger.warning(f"技能包 URL DNS 解析失败 {host}: {e}")
        raise HTTPException(
            status_code=400, detail=ErrorCode.SKILL_PACKAGE_URL_INVALID
        ) from e
    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
        ):
            logger.warning(
                f"技能包 URL 主机 {host} 解析到内网/保留地址 {ip_str}，拒绝"
            )
            raise HTTPException(
                status_code=400, detail=ErrorCode.SKILL_PACKAGE_URL_INVALID
            )

    # 5. 流式下载到临时文件
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=(url_ext.lower() or ".bin"))
    os.close(tmp_fd)
    content_type: Optional[str] = None
    disposition_filename: Optional[str] = None
    first_chunk: Optional[bytes] = None
    total_size = 0
    chunk_size = 1024 * 1024  # 1MB

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            async with client.stream("GET", package_url) as response:
                if response.status_code >= 400:
                    logger.warning(
                        f"技能包 URL 拉取失败 {package_url}: HTTP {response.status_code}"
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=ErrorCode.SKILL_PACKAGE_URL_UNREACHABLE,
                    )
                # Content-Length 预检
                cl = response.headers.get("content-length")
                if cl:
                    try:
                        if int(cl) > max_size:
                            raise HTTPException(
                                status_code=413,
                                detail=ErrorCode.SKILL_PACKAGE_URL_TOO_LARGE,
                            )
                    except ValueError:
                        pass
                content_type = response.headers.get("content-type")
                disposition = response.headers.get("content-disposition", "")
                if disposition:
                    disposition_filename = _filename_from_disposition(disposition)
                with open(tmp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size):
                        if first_chunk is None:
                            first_chunk = chunk
                        total_size += len(chunk)
                        if total_size > max_size:
                            raise HTTPException(
                                status_code=413,
                                detail=ErrorCode.SKILL_PACKAGE_URL_TOO_LARGE,
                            )
                        f.write(chunk)
    except httpx.HTTPError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"技能包 URL 拉取异常 {package_url}: {e}", exc_info=True)
        _safe_remove_tmp(tmp_path)
        raise HTTPException(
            status_code=502, detail=ErrorCode.SKILL_PACKAGE_URL_UNREACHABLE
        ) from e
    except HTTPException:
        _safe_remove_tmp(tmp_path)
        raise

    if total_size == 0:
        _safe_remove_tmp(tmp_path)
        raise HTTPException(
            status_code=400, detail=ErrorCode.SKILL_PACKAGE_URL_INVALID
        )

    # 6. 最终文件名：Content-Disposition 优先于 URL 路径
    final_filename = disposition_filename or url_path_filename
    _, final_ext = os.path.splitext(final_filename)
    is_zip = final_filename.lower().endswith(".zip")

    # 7. 复用既有校验：validate_content_type / check_magic_bytes / scan_file_for_viruses
    try:
        if is_zip:
            # zip 不在 _ALLOWED_CONTENT_TYPES 白名单内，仅做 magic bytes 校验
            if first_chunk and not first_chunk.startswith(b"PK\x03\x04"):
                logger.warning(
                    f"技能包 zip magic bytes 不匹配: {first_chunk[:8]!r}"
                )
                raise HTTPException(
                    status_code=400,
                    detail=ErrorCode.SKILL_PACKAGE_EXTRACT_FAILED,
                )
        else:
            validate_content_type(final_filename, content_type, file_type)
            check_magic_bytes(first_chunk or b"", file_type, final_filename)
        await scan_file_for_viruses(tmp_path)
    except FileUploadError as e:
        _safe_remove_tmp(tmp_path)
        raise HTTPException(
            status_code=e.status_code, detail=_map_file_upload_error(e.error_code)
        ) from e
    except HTTPException:
        _safe_remove_tmp(tmp_path)
        raise
    except OSError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"技能包 URL 文件校验失败: {e}", exc_info=True)
        _safe_remove_tmp(tmp_path)
        raise HTTPException(status_code=500, detail=ErrorCode.FILE_UPLOAD_FAILED) from e

    # 8. zip 解压 or 单文件落盘
    if is_zip:
        try:
            files = _extract_zip_to_upload_root(tmp_path, str(current_user.id))
        except (zipfile.BadZipFile, OSError, PathSecurityError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"技能包 zip 解压失败 {package_url}: {e}", exc_info=True)
            _safe_remove_tmp(tmp_path)
            raise HTTPException(
                status_code=400, detail=ErrorCode.SKILL_PACKAGE_EXTRACT_FAILED
            ) from e
        finally:
            _safe_remove_tmp(tmp_path)

        file_count = len(files)
        total_files_size = sum(f["file_size"] for f in files)
        logger.info(
            f"用户 {current_user.id} 通过 URL 拉取技能包（zip）: "
            f"{package_url} -> {file_count} 个文件"
        )
        await log_audit(
            db, current_user, "upload", "skill_package", package_url,
            request=request,
            details={
                "package_url": package_url,
                "file_count": file_count,
                "total_size": total_files_size,
            },
        )
        await db.commit()

        return success_response({
            "package_url": package_url,
            "files": files,
            "file_count": file_count,
        })

    # 单文件：移动到 UPLOAD_ROOT/<user_id>/<uuid>.<ext>
    safe_filename = f"{uuid.uuid4().hex}{final_ext.lower()}"
    try:
        safe_path = validate_upload_path(str(current_user.id), safe_filename)
        shutil.move(tmp_path, safe_path)
    except PathSecurityError:
        _safe_remove_tmp(tmp_path)
        raise HTTPException(status_code=400, detail=ErrorCode.FILE_PATH_INVALID) from None
    except OSError as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"技能包 URL 文件落盘失败: {e}", exc_info=True)
        _safe_remove_tmp(tmp_path)
        raise HTTPException(status_code=500, detail=ErrorCode.FILE_UPLOAD_FAILED) from e

    rel_path = sanitize_path_for_response(str(safe_path))
    logger.info(
        f"用户 {current_user.id} 通过 URL 拉取技能文件: {package_url} -> {safe_path}"
    )

    await log_audit(
        db, current_user, "upload", "skill_package", package_url,
        request=request,
        details={
            "package_url": package_url,
            "file_count": 1,
            "total_size": total_size,
        },
    )
    await db.commit()

    return success_response({
        "original_name": final_filename,
        "file_path": rel_path,
        "file_size": total_size,
        "file_type": final_ext.lstrip(".").lower() if final_ext else file_type,
    })


@router.post("/upload")
@rate_limit_upload()
async def upload_file(
    request: Request,
    file: UploadFile = File(None),
    package_url: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """通用文件上传端点（multipart/form-data 或 HTTPS URL 拉取）。

    P2-1: 支持 Skill 的文件输入类型（image/audio/video/file）。
    文件保存到 UPLOAD_ROOT/<user_id>/ 目录，返回安全相对路径供后续使用。

    P0-S8: 复用 files.py 同等安全策略：扩展名、MIME、Magic Bytes、
    按类型大小限制、路径安全校验、流式落盘。

    M11: 新增 package_url 表单字段。当 file 未提供且 package_url 提供时，
    后端从 HTTPS URL 流式下载技能包（含 SSRF 防护、大小限制、zip 解压）。
    两者均未提供时返回 400 INVALID_REQUEST。
    """
    if file is None and not package_url:
        raise HTTPException(status_code=400, detail=ErrorCode.INVALID_REQUEST)

    if file is not None:
        return await _handle_multipart_upload(file, request, db, current_user)

    return await _handle_url_fetch(package_url, request, db, current_user)
