"""增量更新服务。

基于 content_hash 比对，只处理变化的文件：
1. 扫描文件夹获取所有文件
2. 查询数据库已有 File 记录
3. 对比 content_hash：
   - 新文件：创建 File 记录 → 处理 → 向量化
   - 已存在但 hash 变化：删除旧向量 → 重新处理 → 更新 File
   - 数据库有但磁盘无：删除 File 记录和向量
4. 返回增量更新统计
"""
import asyncio
import hashlib
import logging
from typing import Callable, Optional

import chromadb.errors
import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.agent import Agent

from app.services.folder_scanner import scan_folder
from app.services.vector_store import VectorStoreService
from app.services.agent_builder import _process_file
from app.utils.metrics import errors_total
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> str:
    """计算文件内容的 SHA256 哈希。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def _process_and_vectorize(
    db: AsyncSession,
    enterprise_id: str,
    agent_id: str,
    path: str,
    scanned,
    vector_store: VectorStoreService,
    file_id: str,
) -> tuple[int, str]:

    """处理单个文件并向量化，返回 (chunk_count, status)。

    P1-RAG: chunk id 使用 `{agent_id}_{file_id}_{chunk_index}` 格式，
    其中 file_id 为数据库 File 记录主键，确保全局唯一，避免跨文件/跨更新冲突。
    """
    chunks = await _process_file(path, scanned.file_type)
    if chunks:
        chunk_ids = [
            f"{agent_id}_{file_id}_{i}" for i in range(len(chunks))
        ]
        metadatas = [
            {
                "enterprise_id": enterprise_id,
                "agent_id": agent_id,
                "source": scanned.name,
                "file_path": path,
                "file_type": scanned.file_type,
                "chunk_index": i,

            }
            for i in range(len(chunks))
        ]
        await vector_store.add_documents(chunks, metadatas, chunk_ids)

    count = len(chunks) if chunks else 0
    return count, "completed"


async def _create_knowledge_snapshot_hook(db: AsyncSession, agent_id: str) -> None:
    """增量更新前创建知识库快照。

    调用 agent_version_service.create_knowledge_snapshot(db, agent_id) 在知识库变更前
    持久化文件列表 / 向量集合名 / 分块策略 / RAG 配置等快照，支撑 6.5 知识库版本管理与回滚。

    本钩子采用懒加载 + 优雅降级：
    - agent_version_service 不可用时（函数不存在），跳过并记录 debug 日志，不阻塞增量更新
    - 快照失败仅记录 warning，不阻断主流程（快照是审计能力，不应让侧效应失败回滚增量更新事务）
    """
    try:
        from app.services.agent_version_service import create_knowledge_snapshot
    except ImportError:
        logger.debug(
            f"知识库快照钩子: agent_version_service.create_knowledge_snapshot 不可用，"
            f"跳过知识库快照（agent={agent_id}）"
        )
        return

    try:
        await create_knowledge_snapshot(db, agent_id)
        logger.info(f"知识库快照钩子: 已创建知识库快照（agent={agent_id}）")
    except (SQLAlchemyError, ValueError, RuntimeError, TypeError) as e:
        # 快照失败不阻塞增量更新：审计能力降级，但主流程继续
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.warning(
            f"知识库快照钩子: 创建知识库快照失败（不阻塞增量更新）: {e}",
            exc_info=True,
        )


async def incremental_update(
    db: AsyncSession,
    agent_id: str,
    folder_path: str,
    progress_callback: Optional[Callable] = None,
    force_reindex: bool = False,
) -> dict:

    """对指定 agent 执行增量更新。

    返回 {"added": int, "updated": int, "deleted": int, "unchanged": int}。
    force_reindex=True 时，即使 content_hash 未变也会在当前（企业前缀）集合中重建向量。

    """
    # 6.5: 更新前创建知识库快照（agent_version_service 不可用时优雅跳过）
    await _create_knowledge_snapshot_hook(db, agent_id)

    # 1. 扫描文件夹
    # 技术审计 R2 H3: scan_folder 内部 os.walk/os.stat 是同步 IO，用 to_thread 避免阻塞事件循环
    scanned_files = await asyncio.to_thread(scan_folder, folder_path)
    disk_files = {f.path: f for f in scanned_files}

    # 2. 查询 Agent 的企业归属并读取已有文件记录。
    # 企业 ID 是向量集合命名与 metadata 二次隔离的必需输入，缺失时拒绝写入。
    agent_result = await db.execute(select(Agent.enterprise_id).where(Agent.id == agent_id))
    enterprise_id = agent_result.scalar_one_or_none()
    if not enterprise_id:
        raise ValueError("agent_not_found_or_missing_enterprise")
    result = await db.execute(select(File).where(File.agent_id == agent_id))

    db_files = {f.file_path: f for f in result.scalars().all()}

    # 3. 对比并执行增量
    added, updated, deleted, unchanged = 0, 0, 0, 0
    # 新写入统一进入企业前缀集合；旧 agent_{id} 集合仅供聊天双读迁移兜底。
    vector_store = await VectorStoreService.create_prefixed(str(enterprise_id), agent_id)

    # 3a. 新增 + 修改
    for path, scanned in disk_files.items():
        try:
            # 技术审计 R2 H3: compute_file_hash 读取整个文件计算 SHA256，大文件可阻塞数秒
            content_hash = await asyncio.to_thread(compute_file_hash, path)
        except OSError as e:
            logger.warning(f"计算文件哈希失败 {path}: {e}")
            continue

        if path not in db_files:
            # 新增
            file_record = File(
                agent_id=agent_id,
                original_name=scanned.name,
                file_path=path,
                file_size=scanned.size,
                file_type=scanned.file_type,
                status="processing",
                content_hash=content_hash,
            )
            db.add(file_record)
            await db.flush()

            try:
                count, status = await _process_and_vectorize(
                    db, str(enterprise_id), agent_id, path, scanned, vector_store, str(file_record.id)

                )
                file_record.status = status
                file_record.chunk_count = count
                file_record.vector_count = count
            except (ValueError, FileNotFoundError, OSError, RuntimeError, chromadb.errors.ChromaError, httpx.HTTPError) as e:
                logger.error(f"增量处理新文件失败 {scanned.name}: {e}", exc_info=True)
                file_record.status = "failed"
                # BE-SEC-02: error_message 会返回给客户端，使用统一错误码
                file_record.error_message = ErrorCode.FILE_PROCESSING_FAILED
                await db.flush()
                added += 1
                continue
            except (TypeError, KeyError, AttributeError) as e:
                errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                logger.error(f"增量处理新文件失败（未预期错误） {scanned.name}: {e}", exc_info=True)
                file_record.status = "failed"
                file_record.error_message = ErrorCode.FILE_PROCESSING_FAILED
                await db.flush()
                added += 1
                continue

            added += 1
        else:
            db_file = db_files[path]
            if force_reindex or db_file.content_hash != content_hash:

                # 修改：删除旧向量，重新处理
                await vector_store.delete_by_metadata({"file_path": path})

                db_file.status = "processing"
                db_file.content_hash = content_hash
                db_file.file_size = scanned.size
                db_file.error_message = None
                await db.flush()

                try:
                    count, status = await _process_and_vectorize(
                        db, str(enterprise_id), agent_id, path, scanned, vector_store, str(db_file.id)

                    )
                    db_file.status = status
                    db_file.chunk_count = count
                    db_file.vector_count = count
                except (ValueError, FileNotFoundError, OSError, RuntimeError, chromadb.errors.ChromaError, httpx.HTTPError) as e:
                    logger.error(f"增量更新文件失败 {scanned.name}: {e}", exc_info=True)
                    db_file.status = "failed"
                    db_file.error_message = ErrorCode.FILE_PROCESSING_FAILED
                    await db.flush()
                    updated += 1
                    continue
                except (TypeError, KeyError, AttributeError) as e:
                    errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                    logger.error(f"增量更新文件失败（未预期错误） {scanned.name}: {e}", exc_info=True)
                    db_file.status = "failed"
                    db_file.error_message = ErrorCode.FILE_PROCESSING_FAILED
                    await db.flush()
                    updated += 1
                    continue

                updated += 1
            else:
                unchanged += 1

    # 3b. 删除（数据库有，磁盘无）
    # W5 修复：补 try/except，避免单条删除失败导致整批回滚
    for path, db_file in db_files.items():
        if path not in disk_files:
            try:
                await vector_store.delete_by_metadata({"file_path": path})
            except (chromadb.errors.ChromaError, RuntimeError, OSError, httpx.HTTPError) as e:
                # 向量删除失败不阻塞 DB 记录删除（向量可后续清理）
                logger.warning(f"删除向量失败（仍删除 DB 记录） {path}: {e}")
            try:
                await db.delete(db_file)
                await db.flush()
                deleted += 1
            except SQLAlchemyError as e:
                logger.error(f"删除 DB 记录失败 {path}: {e}", exc_info=True)
                await db.rollback()

    # 显式 commit：增量更新属于写操作，必须持久化后返回
    await db.commit()
    return {
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged,
    }
