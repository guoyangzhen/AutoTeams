"""编译器 API 路由（spec.md §10.7 WT1 部分）。

端点清单（无尾斜杠，路径遵循 spec.md §10.7 契约）：
- POST   /compiler/compile                          触发五级编译（异步，返回 job_id）
- POST   /compiler/recompile                        增量重编译
- GET    /compiler/jobs                             编译任务列表
- GET    /compiler/jobs/{job_id}                    编译任务详情（含 error_message）
- GET    /compiler/jobs/{job_id}/stream             编译进度 SSE 推流（UI v4）
- GET    /compiler/animation/{job_id}               编译动画数据
- GET    /compiler/replay/{enterprise_id}           编译回放数据（UI v4 路演用）
- GET    /compiler/completeness/{enterprise_id}     完成度查询
"""

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.models.compiler import CompilationJob, CompilationArtifact
from app.models.enterprise import Enterprise
from app.models.user import User

from app.schemas.compiler import (
    CompileRequest,
    CompileResponse,
    RecompileRequest,
    CompilationJobResponse,
    CompilationJobListResponse,
    CompletenessResponse,
    AnimationResponse,
    AnimationStage,
)
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.services.compiler.job_queue import enqueue_compilation_job

from app.services.compiler.completeness import CompletenessCalculator
from app.services.compiler.base import CompilationContext
from app.services.cognition.progressive_modeler import ProgressiveModeler
from app.services.path_security import validate_path
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/compiler", tags=["编译器"])


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    """校验企业管理员权限。"""
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id or current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


async def _ensure_active_enterprise(db: AsyncSession, enterprise_id: str) -> None:
    """拒绝为不存在或停用企业创建异步任务，保持 API 与队列归属一致。"""
    result = await db.execute(select(Enterprise).where(Enterprise.id == enterprise_id))
    enterprise = result.scalar_one_or_none()
    if enterprise is None or not enterprise.is_active:
        raise HTTPException(status_code=404, detail=ErrorCode.ENTERPRISE_NOT_FOUND)


@router.post("/compile", response_model=None)

@rate_limit_api()
async def compile_enterprise(
    request: Request,
    data: CompileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发五级编译（PRD §5.2 编译交互流程）。

    异步执行：创建编译任务后立即返回 job_id，编译在后台进行。
    前端通过 GET /compiler/jobs/{job_id} 轮询状态，通过 GET /compiler/animation/{job_id} 查看进度。
    """
    _verify_enterprise_admin(current_user, data.enterprise_id)
    await _ensure_active_enterprise(db, data.enterprise_id)

    # 校验文件夹路径（如有）—— path_security.validate_path 防目录穿越

    # folder_path 为空时，自动使用示例企业数据目录作为默认数据源
    folder_path = request.query_params.get("folder_path", "")
    is_default_path = False
    if not folder_path:
        # 默认使用示例企业数据（服务端配置路径，无需用户路径校验）
        default_path = os.path.join(settings.SAMPLE_DATA_DIR, "example-enterprise")
        if os.path.isdir(default_path):
            folder_path = default_path
            is_default_path = True
            logger.info(f"未指定 folder_path，使用默认示例数据: {folder_path}")
    if folder_path and not is_default_path:
        try:
            folder_path = validate_path(folder_path, must_exist=True)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"无效的文件夹路径: {e}",
            ) from e

    # 数据源标记：用户显式指定 folder_path 时记为 user_upload，否则为 default（演示数据）
    # 供前端在编译结果展示「数据源：用户上传文件夹 xxx」，仅当确有用户上传数据源时显示
    source = "user_upload" if (folder_path and not is_default_path) else "default"
    folder_name = os.path.basename(folder_path.rstrip("/\\")) if folder_path else None

    # 1. 先将任务输入快照持久化为 queued Job，再由独立 Worker 领取执行。
    # 相同企业/输入的活跃任务会复用，避免双击、网络重试和多实例导致重复编译。
    job, created = await enqueue_compilation_job(
        db,
        enterprise_id=data.enterprise_id,
        folder_path=folder_path,
        trigger_source=data.trigger_source,
    )

    await log_audit(
        db, current_user, "compile", "enterprise", data.enterprise_id,
        request=request,
        details={
            "trigger": data.trigger_source,
            "job_id": job.id,
            "queued": created,
            "deduplicated": not created,
        },
    )
    await db.commit()

    # 2. 立即返回持久任务 ID；Worker 状态和进度通过原有查询/SSE 接口读取。
    return success_response(
        data={
            "job_id": job.id,
            "status": job.status,
            "deduplicated": not created,
            # 数据源标记：仅当用户显式上传文件夹时 source=user_upload，否则 default（演示数据）
            "source": source,
            # 实际使用的数据源文件夹名（仅 basename，不泄露服务器绝对路径）
            "folder_name": folder_name,
        },
        message="编译任务已排队，请轮询任务状态" if created else "相同编译任务已在队列中执行",
    )


@router.post("/recompile", response_model=None)
@rate_limit_api()
async def recompile_enterprise(
    request: Request,
    data: RecompileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """增量重编译（渐进式建模触发）。"""
    _verify_enterprise_admin(current_user, data.enterprise_id)
    await _ensure_active_enterprise(db, data.enterprise_id)

    # 新输入目录必须经过与全量编译一致的安全校验；省略时仅复用最近一次

    # completed 编译已持久化的目录快照，绝不创建 Worker 无法执行的空输入任务。
    folder_path = data.folder_path
    if folder_path:
        try:
            folder_path = validate_path(folder_path, must_exist=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无效的文件夹路径: {exc}") from exc

    modeler = ProgressiveModeler(db, data.enterprise_id)
    try:
        job = await modeler.trigger_recompile(
            data.trigger_source,
            data.affected_stages,
            folder_path=folder_path,
        )
    except ValueError as exc:
        if str(exc) == "recompile_requires_folder_path_or_completed_compilation":
            raise HTTPException(
                status_code=409,
                detail="没有可复用的已完成编译输入，请提供 folder_path 后重试",
            ) from exc
        raise

    await log_audit(
        db, current_user, "recompile", "enterprise", data.enterprise_id,
        request=request,
        details={"job_id": job.id, "trigger": data.trigger_source},
    )

    return success_response(
        data=CompileResponse(job_id=job.id, status=job.status).model_dump(),
        message="增量重编译任务已创建",
    )


@router.get("/jobs", response_model=None)
@rate_limit_api()
async def list_compilation_jobs(
    request: Request,
    enterprise_id: str = Query(..., description="企业 ID"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编译任务列表（分页）。"""
    _verify_enterprise_admin(current_user, enterprise_id)

    # 查询总数
    count_result = await db.execute(
        select(func.count(CompilationJob.id)).where(
            CompilationJob.enterprise_id == enterprise_id
        )
    )
    total = count_result.scalar() or 0

    # 查询分页数据
    result = await db.execute(
        select(CompilationJob)
        .where(CompilationJob.enterprise_id == enterprise_id)
        .order_by(CompilationJob.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    jobs = result.scalars().all()

    items = [
        CompilationJobResponse(
            job_id=j.id,
            enterprise_id=j.enterprise_id,
            stage=j.stage,
            status=j.status,
            progress=j.progress or 0.0,
            confidence=j.confidence,
            completeness=j.completeness,
            started_at=j.started_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]

    return success_response(
        data=CompilationJobListResponse(items=items, total=total).model_dump()
    )


@router.get("/jobs/{job_id}", response_model=None)
@rate_limit_api()
async def get_compilation_job(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编译任务详情。"""
    result = await db.execute(
        select(CompilationJob).where(CompilationJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)

    _verify_enterprise_admin(current_user, job.enterprise_id)

    # 加载 artifacts
    artifact_result = await db.execute(
        select(CompilationArtifact)
        .where(CompilationArtifact.job_id == job_id)
        .order_by(CompilationArtifact.created_at.asc())
    )
    artifacts = artifact_result.scalars().all()

    stage_results = {}
    for a in artifacts:
        stage_results[a.stage] = {
            "confidence": a.confidence,
            "discovered_summary": a.discovered_summary,
            "duration_ms": a.duration_ms,
        }

    response = CompilationJobResponse(
        job_id=job.id,
        enterprise_id=job.enterprise_id,
        stage=job.stage,
        status=job.status,
        progress=job.progress or 0.0,
        confidence=job.confidence,
        completeness=job.completeness,
        result=stage_results,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )

    return success_response(data=response.model_dump(mode="json"))


@router.get("/jobs/{job_id}/stream", response_model=None)
async def stream_compilation_progress(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编译进度 SSE 推流（UI v4 §七 P0-4）。

    替代前端 3 秒轮询，让五级管道动画平滑推进。
    事件负载与 GET /jobs/{job_id} 同构，前端可无缝降级到轮询
    （路演现场网络受限时的保命路径）。

    每帧：{job_id, stage, status, progress, confidence, completeness,
           stages:[{name,status,discovered,confidence,duration_ms}], done}
    """
    result = await db.execute(select(CompilationJob).where(CompilationJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)
    _verify_enterprise_admin(current_user, job.enterprise_id)

    # 提前释放请求 session：SSE 可能持续数分钟，不应占用连接池
    await db.close()

    async def event_generator():
        last_signature = None
        elapsed = 0.0
        # 编译最长 20 分钟，超时后主动收尾避免连接泄漏
        max_seconds = 1500.0
        interval = 1.0
        try:
            while elapsed < max_seconds:
                if await request.is_disconnected():
                    break

                async with async_session_factory() as sdb:
                    jr = await sdb.execute(
                        select(CompilationJob).where(CompilationJob.id == job_id)
                    )
                    j = jr.scalar_one_or_none()
                    if j is None:
                        yield f"data: {json.dumps({'error': '任务不存在', 'done': True}, ensure_ascii=False)}\n\n"
                        return

                    ar = await sdb.execute(
                        select(CompilationArtifact)
                        .where(CompilationArtifact.job_id == job_id)
                        .order_by(CompilationArtifact.created_at.asc())
                    )
                    artifacts = list(ar.scalars().all())

                stages = _build_animation_stages(artifacts, j.stage, j.status)
                payload = {
                    "job_id": j.id,
                    "enterprise_id": j.enterprise_id,
                    "stage": j.stage,
                    "status": j.status,
                    "progress": j.progress or 0.0,
                    "confidence": j.confidence,
                    "completeness": j.completeness,
                    "error_message": j.error_message,
                    "stages": stages,
                    "done": j.status in ("completed", "failed"),
                }

                # 仅在状态变化时推送，避免无谓流量
                signature = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
                if signature != last_signature:
                    last_signature = signature
                    yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

                if payload["done"]:
                    return

                await asyncio.sleep(interval)
                elapsed += interval
            # 超时收尾
            yield f"data: {json.dumps({'done': True, 'timeout': True}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("编译进度 SSE 异常: job=%s error=%s", job_id, e, exc_info=True)
            yield f"data: {json.dumps({'error': '进度推送中断', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_animation_stages(
    artifacts: list, current_stage: str, job_status: str
) -> list[dict]:
    """构建五级动画阶段数据（SSE 与 /animation 端点共用）。

    修复审计问题：原实现把 failed 状态并入 pending，失败阶段与未开始阶段
    视觉上无法区分，且 error_message 无处呈现。
    """
    order = ["information", "knowledge", "process", "capability", "runtime"]
    stages: list[dict] = []
    for name in order:
        artifact = next((a for a in artifacts if a.stage == name), None)
        if artifact:
            stages.append({
                "name": name,
                "status": "completed",
                "discovered": artifact.discovered_summary or "",
                "confidence": artifact.confidence,
                "duration_ms": artifact.duration_ms,
            })
        elif name == current_stage:
            # 当前阶段：编译失败时标记为 failed（此前被错误归为 pending）
            stages.append({
                "name": name,
                "status": "failed" if job_status == "failed" else "running",
                "discovered": "",
                "confidence": None,
                "duration_ms": None,
            })
        else:
            stages.append({
                "name": name,
                "status": "pending",
                "discovered": "",
                "confidence": None,
                "duration_ms": None,
            })
    return stages


@router.get("/animation/{job_id}", response_model=None)
@rate_limit_api()
async def get_compilation_animation(
    request: Request,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编译动画数据（PRD §4.3 编译动画展示，spec.md §10.7 路径契约）。"""
    result = await db.execute(
        select(CompilationJob).where(CompilationJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=ErrorCode.NOT_FOUND)

    _verify_enterprise_admin(current_user, job.enterprise_id)

    # 加载 artifacts 构建动画数据
    artifact_result = await db.execute(
        select(CompilationArtifact)
        .where(CompilationArtifact.job_id == job_id)
        .order_by(CompilationArtifact.created_at.asc())
    )
    artifacts = list(artifact_result.scalars().all())

    # name 返回英文阶段标识符（前端 CompilerStageName 字面量匹配用）；
    # 中文展示标签由前端按 stage name 本地化映射。
    # 复用 _build_animation_stages 与 SSE 保持一致（含 failed 态区分）
    stages = [
        AnimationStage(
            name=s["name"],
            status=s["status"],
            discovered=s["discovered"],
            confidence=s["confidence"],
            duration_ms=s["duration_ms"],
        )
        for s in _build_animation_stages(artifacts, job.stage, job.status)
    ]

    return success_response(
        data=AnimationResponse(stages=stages).model_dump()
    )


@router.get("/replay/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_compilation_replay(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编译回放数据（UI v4 §九 问题5 —— 路演演示方案）。

    背景：真实五级编译需 10-20 分钟，路演/评委远程体验时无法现场等待。
    本端点返回**最近一次成功编译的真实产物与真实时序**，
    前端按 duration_ms 的相对比例加速回放五级管道动画。

    与「造假」的本质区别：
      - 数据 100% 来自真实编译产物（discovered_summary / confidence / 耗时）
      - 仅压缩时间轴，不虚构任何内容
      - 前端明确标注「回放」状态，不冒充实时编译

    无历史编译时返回 available=false，前端据此隐藏回放入口（诚实空态）。
    """
    _verify_enterprise_admin(current_user, enterprise_id)

    job_result = await db.execute(
        select(CompilationJob)
        .where(
            CompilationJob.enterprise_id == enterprise_id,
            CompilationJob.status == "completed",
        )
        .order_by(CompilationJob.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        return success_response(
            data={"available": False, "stages": [], "reason": "尚无成功的编译记录"}
        )

    artifact_result = await db.execute(
        select(CompilationArtifact)
        .where(CompilationArtifact.job_id == job.id)
        .order_by(CompilationArtifact.created_at.asc())
    )
    artifacts = list(artifact_result.scalars().all())
    if not artifacts:
        return success_response(
            data={"available": False, "stages": [], "reason": "该编译无产物记录"}
        )

    stages = _build_animation_stages(artifacts, job.stage, job.status)
    total_ms = sum(s["duration_ms"] or 0 for s in stages)

    return success_response(
        data={
            "available": True,
            "job_id": job.id,
            "enterprise_id": enterprise_id,
            "stages": stages,
            "confidence": job.confidence,
            "completeness": job.completeness,
            "total_duration_ms": total_ms,
            "compiled_at": job.completed_at.isoformat() if job.completed_at else None,
        }
    )


@router.get("/completeness/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_completeness(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """完成度查询（PRD §4.3 五维加权）。"""
    _verify_enterprise_admin(current_user, enterprise_id)

    # 从最近的编译任务加载 artifacts
    job_result = await db.execute(
        select(CompilationJob)
        .where(CompilationJob.enterprise_id == enterprise_id)
        .order_by(CompilationJob.created_at.desc())
        .limit(1)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        return success_response(data=CompletenessResponse(
            overall=0.0,
            dimensions={},
            level="incomplete",
            gaps=[],
        ).model_dump())

    # 加载 artifacts
    artifact_result = await db.execute(
        select(CompilationArtifact)
        .where(CompilationArtifact.job_id == job.id)
        .order_by(CompilationArtifact.created_at.desc())
    )
    upstream = {}
    seen: set[str] = set()
    for a in artifact_result.scalars():
        if a.stage not in seen:
            seen.add(a.stage)
            upstream[a.stage] = a.output

    ctx = CompilationContext(enterprise_id=enterprise_id, db=db, upstream=upstream)
    calculator = CompletenessCalculator(enterprise_id)
    completeness = calculator.calculate(ctx)
    gaps = calculator.to_gaps_contract(completeness)

    return success_response(data=CompletenessResponse(
        overall=completeness.overall,
        dimensions=completeness.dimensions,
        level=completeness.level,
        gaps=gaps.gaps,
    ).model_dump(mode="json"))
