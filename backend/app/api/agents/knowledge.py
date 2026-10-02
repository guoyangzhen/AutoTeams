"""Agent 知识库管理与文件监控端点 (3.3.3 拆分)。

涵盖职责：
- GET  /agents/{id}/knowledge-stats：知识库统计（文件类型/时间线/状态/总数）
- POST /agents/{id}/rollback-knowledge/{version_id}：回滚知识库快照
- POST /agents/{id}/watch：启动文件监控
- DELETE /agents/{id}/watch：停止文件监控
- POST /agents/{id}/incremental-update：触发增量更新
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.file import File
from app.models.user import User
from app.schemas.agent import AgentResponse
from app.utils.security import get_current_user
from app.utils.rbac import require_admin
from app.utils.audit import log_audit
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.metrics import errors_total
from app.utils.error_codes import ErrorCode

from app.api.agents._helpers import (
    _get_agent_or_404,
    _get_agent_or_404_for_watching,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agent"])

# 文件类型映射：英文 → (中文名, 颜色)
FILE_TYPE_MAP = {
    "document": ("文档", "#6366F1"),
    "image": ("图片", "#10B981"),
    "video": ("视频", "#F59E0B"),
    "audio": ("音频", "#EF4444"),
    "code": ("代码", "#3B82F6"),
    "data": ("数据", "#8B5CF6"),
}

# 文件状态映射：英文 → 中文名
FILE_STATUS_MAP = {
    "completed": "已完成",
    "processing": "处理中",
    "uploaded": "待处理",
    "failed": "失败",
}


@router.get("/{agent_id}/knowledge")
@rate_limit_api()
async def get_agent_knowledge_export(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """导出 Agent 知识库的原始文本内容（供协作工作台沙箱使用）。

    协作工作台（collaboration-service）在创建会话时调用本端点，把该 AI 员工
    的知识文档物化到沙箱的 knowledge/ 目录，使技能（product-knowledge 等）真正
    可检索、可回答。返回 { files: [{name, content}] }。

    读取顺序：
    1. agent.folder_path 指向的目录（文本类文件）
    2. 回退到 File 记录实际落盘的文件
    3. 均无 → 返回空列表（协作服务会回退到内置企业知识种子）
    """
    agent = await _get_agent_or_404_for_watching(db, agent_id, current_user)

    files: list[dict] = []
    seen: set[str] = set()

    def _collect_file(name: str, abs_path: str) -> None:
        if name in seen or not os.path.isfile(abs_path):
            return
        if not _is_text_ext(name):
            return
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            return
        if content and content.strip():
            files.append({"name": name, "content": content})
            seen.add(name)

    # 1. agent.folder_path 目录
    folder = agent.folder_path
    if folder:
        abs_folder = os.path.abspath(str(folder))
        if os.path.isdir(abs_folder):
            try:
                for name in sorted(os.listdir(abs_folder)):
                    _collect_file(name, os.path.join(abs_folder, name))
            except OSError:
                pass

    # 2. File 记录落盘文件
    if not files:
        result = await db.execute(
            select(File)
            .where(File.agent_id == agent_id, File.status == "completed")
            .order_by(File.created_at)
        )
        for rec in result.scalars().all():
            if rec.file_path:
                _collect_file(rec.original_name, os.path.abspath(str(rec.file_path)))

    return success_response({"files": files, "total": len(files)})


def _is_text_ext(name: str) -> bool:
    """判断是否为可物化为文本的知识文件扩展名。"""
    ext = os.path.splitext(name)[1].lower()
    return ext in {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html", ".xml", ".log"}


@router.get("/{agent_id}/knowledge-stats")
@rate_limit_api()
async def get_agent_knowledge_stats(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 Agent 知识库的统计数据：文件类型分布、时间线、状态分布、总文件数与分块数。

    3.2.1: 合并 5 次串行 DB 查询为 2 次。
    - Query 1: 按 (file_type, status) 联合 GROUP BY，一次拿到 type_counts + status_counts + total_files + total_chunks
    - Query 2: 时间线（需日期过滤，单独查询）
    原 5 次 RTT 降为 2 次，p99 延迟显著降低。
    """
    agent = await _get_agent_or_404_for_watching(db, agent_id, current_user)

    # 3.2.1: Query 1 — 联合聚合，一次拿到 type_counts + status_counts + total_files + total_chunks
    # GROUP BY (file_type, status) 后在 Python 端二次聚合（按 type 与按 status 分别累加）
    combined_result = await db.execute(
        select(
            File.file_type,
            File.status,
            func.count(File.id),
            func.sum(File.chunk_count),
        )
        .where(File.agent_id == agent_id)
        .group_by(File.file_type, File.status)
    )
    type_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    total_files = 0
    total_chunks = 0
    for ftype, status, cnt, chunks in combined_result.all():
        if ftype is not None:
            type_counts[ftype] = type_counts.get(ftype, 0) + cnt
        if status is not None:
            status_counts[status] = status_counts.get(status, 0) + cnt
        total_files += cnt
        total_chunks += int(chunks or 0)
    total_files = int(total_files)
    total_chunks = int(total_chunks)

    # 3.2.1: Query 2 — 时间线（需日期过滤，无法合并到 Query 1）
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    date_col = func.date(File.created_at)
    timeline_result = await db.execute(
        select(date_col, func.sum(File.chunk_count))
        .where(File.agent_id == agent_id, File.created_at >= seven_days_ago)
        .group_by(date_col)
        .order_by(date_col)
    )
    knowledge_timeline = []
    for d, c in timeline_result.all():
        if d is None:
            date_str = ""
        elif isinstance(d, str):
            # SQLite 的 date() 返回 "2025-07-01" 字符串
            date_str = d[5:10] if len(d) >= 10 else d
        else:
            # PostgreSQL 返回 date 对象
            date_str = d.strftime("%m-%d")
        knowledge_timeline.append({"date": date_str, "count": int(c or 0)})

    # 构建 fileTypeDistribution（按 FILE_TYPE_MAP 顺序，仅包含存在的类型）
    file_type_distribution = []
    for ftype, (name, color) in FILE_TYPE_MAP.items():
        count = type_counts.get(ftype, 0)
        if count > 0:
            file_type_distribution.append({"name": name, "value": count, "color": color})

    # 构建 fileStatus（按 FILE_STATUS_MAP 顺序，仅包含存在的状态）
    file_status = []
    for status, name in FILE_STATUS_MAP.items():
        count = status_counts.get(status, 0)
        if count > 0:
            file_status.append({"name": name, "value": count})

    return success_response({
        "fileTypeDistribution": file_type_distribution,
        "knowledgeTimeline": knowledge_timeline,
        "fileStatus": file_status,
        "totalFiles": total_files,
        "totalChunks": total_chunks,
        "agentName": agent.name,
    })


@router.post("/{agent_id}/rollback-knowledge/{version_id}")
@rate_limit_api()
async def rollback_agent_knowledge_api(
    agent_id: str,
    version_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """回滚 Agent 知识库到指定历史版本快照。

    与配置回滚（/agents/{id}/versions/{vid}/rollback）不同，本端点回滚的是
    知识库状态（文件列表 / 向量集合 / 分块策略 / RAG 配置），由
    agent_version_service.rollback_knowledge 实现。

    仅管理员可执行知识库回滚。
    """
    agent = await _get_agent_or_404(db, agent_id, current_user)

    # D3/D4 已合并：rollback_knowledge 在 agent_version_service 中实现
    # 保留懒加载/ImportError 兜底，防止极端情况下模块缺失导致启动失败
    try:
        from app.services.agent_version_service import rollback_knowledge
    except ImportError:
        logger.info(
            f"rollback-knowledge 路由已就绪，但 agent_version_service.rollback_knowledge "
            f"不可用（agent={agent_id}）"
        )
        raise HTTPException(
            status_code=501,
            detail=ErrorCode.OPERATION_FAILED,
        ) from None

    try:
        await rollback_knowledge(db, agent, version_id, current_user.id)
    except ValueError as e:
        if str(e) == "version_not_found":
            raise HTTPException(status_code=404, detail=ErrorCode.AGENT_VERSION_NOT_FOUND) from e
        raise HTTPException(status_code=400, detail=ErrorCode.AGENT_VERSION_ROLLBACK_FAILED) from e
    except SQLAlchemyError as e:
        logger.error(f"回滚 Agent 知识库失败: {e}", exc_info=True)
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        raise HTTPException(status_code=500, detail=ErrorCode.AGENT_VERSION_ROLLBACK_FAILED) from e

    await db.refresh(agent)
    await log_audit(
        db, current_user, "rollback_knowledge", "agent", agent_id, request=request,
        details={"rollback_to_version_id": version_id},
    )
    await db.commit()
    return success_response(AgentResponse.model_validate(agent).model_dump())


# ============================================================
# P2-4: 文件监控与增量更新端点
# ============================================================

@router.post("/{agent_id}/watch")
@rate_limit_api()
async def start_file_watching(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """启动文件监控，监控 agent 关联文件夹的变化。"""
    from app.services.file_watcher import file_watcher_service

    await _get_agent_or_404_for_watching(db, agent_id, current_user)

    if not file_watcher_service.is_available():
        raise HTTPException(
            status_code=503,
            detail=ErrorCode.AGENT_WATCHDOG_UNAVAILABLE,
        )

    # 从关联的文件记录中获取文件夹路径
    folder_path = await _get_agent_folder_path(db, agent_id)
    if not folder_path:
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.AGENT_FOLDER_MISSING,
        )

    started = await file_watcher_service.start_watching(agent_id, folder_path)
    if not started:
        raise HTTPException(
            status_code=500,
            detail=ErrorCode.AGENT_WATCH_FAILED,
        )

    # P1/P2-INFRA: 记录启动文件监控审计日志
    await log_audit(
        db, current_user, "start_watching", "agent", agent_id,
        request=request,
        details={"folder_path": folder_path},
    )
    await db.commit()

    return success_response({
        "agent_id": agent_id,
        "folder_path": folder_path,
        "watching": True,
    })


@router.delete("/{agent_id}/watch")
@rate_limit_api()
async def stop_file_watching(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """停止文件监控。"""
    from app.services.file_watcher import file_watcher_service

    await _get_agent_or_404_for_watching(db, agent_id, current_user)
    await file_watcher_service.stop_watching(agent_id)

    # P1/P2-INFRA: 记录停止文件监控审计日志
    await log_audit(
        db, current_user, "stop_watching", "agent", agent_id,
        request=request,
    )
    await db.commit()

    return success_response({
        "agent_id": agent_id,
        "watching": False,
    })


@router.post("/{agent_id}/incremental-update")
@rate_limit_api()
async def trigger_incremental_update(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """触发增量更新：基于 content_hash 比对，只处理变化的文件。"""
    from app.services.incremental_updater import incremental_update

    await _get_agent_or_404_for_watching(db, agent_id, current_user)

    folder_path = await _get_agent_folder_path(db, agent_id)
    if not folder_path:
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.AGENT_FOLDER_MISSING,
        )

    stats = await incremental_update(db, agent_id, folder_path)

    # P1/P2-INFRA: 记录增量更新审计日志
    await log_audit(
        db, current_user, "incremental_update", "agent", agent_id,
        request=request,
        details={"folder_path": folder_path, "stats": stats},
    )
    await db.commit()

    return success_response({
        "agent_id": agent_id,
        "folder_path": folder_path,
        "stats": stats,
    })


async def _get_agent_folder_path(db: AsyncSession, agent_id: str) -> str | None:
    """BE-REL-04: 优先读取 Agent 记录持久化的 folder_path，避免依赖 File 记录推导。

    兼容旧数据：若 agent.folder_path 为空，则回退到第一个 File 记录的所在目录。
    """
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if agent and agent.folder_path:
        return agent.folder_path

    # 兼容旧数据
    result = await db.execute(
        select(File).where(File.agent_id == agent_id).limit(1)
    )
    file_rec = result.scalar_one_or_none()
    if file_rec:
        return os.path.dirname(file_rec.file_path)
    return None
