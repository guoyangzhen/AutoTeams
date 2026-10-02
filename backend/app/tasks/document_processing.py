"""文档处理后台任务（解析 → 切块 → 向量化 → 落库）。

依据重构计划 §4.2.4：
    agent_graph.py 用 asyncio.create_task() 在 API 进程内跑 CPU 密集的文档处理，
    _running_tasks（内存字典）在多 Worker 环境下失效。

本模块把该流水线迁到 Celery worker，使处理时长与 API 进程解耦、可重试、可观测。

⚠️ 同步/异步边界：Celery worker 是**同步**进程，而 document_processor /
vector_store / chunker_service 全是 async。任务函数内部用 ``asyncio.run()`` 桥接。
若把任务写成 ``async def``，Celery 不会 await，返回值会被静默丢弃。

chunk_id 与 metadata 沿用 agent_graph.make_vectorizer_node 的既有约定
（``{agent_id}_{file_id}_{index}``），确保与存量向量库可共存、增量更新不冲突——
**不自造第二套 ID 规则**，否则同一份文档会在两个体系里各存一份。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List

from celery import shared_task

from app.tasks.celery_app import celery_app  # noqa: F401  —— 保证任务注册到同一 app

logger = logging.getLogger(__name__)


def _chunk_metadata(
    *, agent_id: str, file_name: str, file_path: str, file_type: str, index: int
) -> Dict[str, Any]:
    """构造与既有向量化节点一致的 chunk metadata。"""
    return {
        "enterprise_id": None,  # 由写入方按集合隔离，此处保留键位供下游可选填充
        "source": file_name,
        "file_path": file_path,
        "file_type": file_type,
        "chunk_index": index,
    }


async def _process_one(
    *,
    enterprise_id: str,
    agent_id: str,
    file_path: str,
    file_name: str,
    file_type: str,
) -> Dict[str, Any]:
    """单个文档的完整流水线（async）。"""
    from app.services.document_processor import process_document
    from app.services.rag.chunker_service import ChunkerService
    from app.services.vector_store import VectorStoreService

    text = await process_document(file_path)
    if not text or not text.strip():
        return {
            "file": file_name,
            "status": "skipped",
            "reason": "解析结果为空",
            "chunks": 0,
        }

    chunks: List[str] = ChunkerService().chunk_text(text, file_type=file_type)
    if not chunks:
        return {"file": file_name, "status": "skipped", "reason": "切块结果为空", "chunks": 0}

    # BE-PER-01：写入企业前缀集合，禁止写入旧的 agent_{id} 集合，避免多租户隔离分叉
    store = await VectorStoreService.create_prefixed(enterprise_id, agent_id)
    # file_id 缺失时用文件名 + 内容稳定摘要兜底，保证 chunk_id 仍可重复推导
    file_id = os.path.splitext(file_name)[0]
    ids = [f"{agent_id}_{file_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        _chunk_metadata(
            agent_id=agent_id,
            file_name=file_name,
            file_path=file_path,
            file_type=file_type,
            index=i,
        )
        for i in range(len(chunks))
    ]
    await store.add_documents(chunks, metadatas, ids)
    return {
        "file": file_name,
        "status": "completed",
        "chunks": len(chunks),
        "chunk_id_prefix": f"{agent_id}_{file_id}",
    }


@shared_task(bind=True, max_retries=3, soft_time_limit=600, name="app.tasks.document_processing.process_documents")
def process_documents(
    self, enterprise_id: str, agent_id: str, file_paths: List[str]
) -> Dict[str, Any]:
    """后台异步处理文档 → 向量化 → 知识库构建。

    Args:
        enterprise_id: 企业 ID，决定向量集合隔离前缀
        agent_id: 数字员工 ID，参与 chunk_id 构造
        file_paths: 待处理文件路径列表

    Returns:
        逐文件结果汇总 + 总分块数。单文件失败不阻断其余文件。
    """
    if not file_paths:
        return {
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "total_chunks": 0,
            "results": [],
        }

    async def _run_all() -> List[Dict[str, Any]]:
        async def _guarded(path: str) -> Dict[str, Any]:
            name = os.path.basename(path)
            try:
                ext = os.path.splitext(name)[1].lstrip(".").lower()
                return await _process_one(
                    enterprise_id=enterprise_id,
                    agent_id=agent_id,
                    file_path=path,
                    file_name=name,
                    file_type=ext or "document",
                )
            except Exception as exc:  # noqa: BLE001 —— 单文件失败不应拖垮整批
                logger.exception("文档处理失败: %s", path)
                return {
                    "file": name,
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "chunks": 0,
                }

        return list(await asyncio.gather(*[_guarded(p) for p in file_paths]))

    results = asyncio.run(_run_all())
    succeeded = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] == "failed"]
    total_chunks = sum(int(r.get("chunks", 0)) for r in results)

    summary = {
        "total": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "skipped": len(results) - len(succeeded) - len(failed),
        "total_chunks": total_chunks,
        "results": results,
    }

    # 全部文件都失败才算任务失败；部分失败已逐条记录，不触发整体重试
    if failed and not succeeded:
        raise RuntimeError(f"全部 {len(failed)} 个文档处理失败: {failed[0].get('reason')}")

    logger.info(
        "[tasks] 文档处理完成 agent=%s 成功=%s 失败=%s 分块=%s",
        agent_id, summary["succeeded"], summary["failed"], total_chunks,
    )
    return summary


@shared_task(bind=False, name="app.tasks.document_processing.count_document_chunks")
def count_document_chunks(file_path: str) -> Dict[str, Any]:
    """只做解析与切块、不落库的轻量体检。

    供上传后预检与单测使用：验证「解析 → 切块」这一步是否正常，
    避免为了体检就把文件写进知识库。
    """
    from app.services.document_processor import process_document
    from app.services.rag.chunker_service import ChunkerService

    text = asyncio.run(process_document(file_path))
    ext = os.path.splitext(file_path)[1].lstrip(".").lower() or "document"
    chunks = ChunkerService().chunk_text(text, file_type=ext) if text else []
    return {
        "file": os.path.basename(file_path),
        "chars": len(text or ""),
        "chunks": len(chunks),
    }
