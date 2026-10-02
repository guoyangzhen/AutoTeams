"""Agent 版本快照服务（P0-2 + D4 6.5）。

在 update_agent 时保存配置快照到 agent_versions 表，支持版本回滚与历史追溯。

设计：
- 快照内容：name / description / system_prompt / config（Agent.config JSON）/ version
- 版本号策略：语义化版本 patch 递增（1.0.0 → 1.0.1 → 1.0.2）
- 活跃状态：每次创建新快照时，旧快照的 is_active 全部置 false，新快照 is_active=True
- 回滚：从历史快照恢复 Agent 字段，并创建一个新的快照记录"回滚到 vx.y.z"操作

D4 6.5 扩展：
- create_knowledge_snapshot：增量更新前持久化知识库状态快照
  （文件列表 / 向量集合名 / 分块策略 / RAG 配置），is_active=False
- rollback_knowledge：从 knowledge_snapshot 恢复 RAG/分块配置，并创建回滚记录
"""
import json
import logging
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.file import File

logger = logging.getLogger(__name__)


def _bump_version(current_version: str) -> str:
    """递增版本号的 patch 位：'1.0.0' → '1.0.1'。

    对无法解析的版本号，回退为在末尾追加 '-next'。
    """
    if not current_version:
        return "1.0.1"
    parts = current_version.split(".")
    if len(parts) == 3 and parts[2].isdigit():
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return f"{current_version}-next"


def _build_snapshot(agent: Agent) -> dict:
    """从 Agent 当前字段构建配置快照。"""
    return {
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "config": agent.config or {},
        "version": agent.version,
    }


async def create_version_snapshot(
    db: AsyncSession,
    agent: Agent,
    user_id: Optional[str],
    changelog: Optional[str] = None,
) -> AgentVersion:
    """将 Agent 当前配置保存为版本快照，并递增 Agent.version。

    调用时机：在 update_agent 应用新值之前调用，以捕获"旧配置"。
    快照保存后，agent.version 会被递增（调用方负责将新 version 持久化）。

    流程：
    1. 将现有活跃快照置为 is_active=False
    2. 用当前 agent 字段创建新的 AgentVersion（is_active=True）
    3. 递增 agent.version（patch 位 +1）
    4. flush（不 commit，由调用方负责）
    """
    try:
        # 1. 旧活跃快照置为 inactive
        await db.execute(
            update(AgentVersion)
            .where(AgentVersion.agent_id == agent.id, AgentVersion.is_active.is_(True))
            .values(is_active=False)
        )

        # 2. 创建新快照（捕获当前/旧配置）
        snapshot = AgentVersion(
            agent_id=agent.id,
            version=agent.version or "1.0.0",
            config_snapshot=_build_snapshot(agent),
            changelog=changelog,
            is_active=True,
            user_id=user_id,
        )
        db.add(snapshot)

        # 3. 递增 agent.version（新版本号代表"更新后"的版本）
        agent.version = _bump_version(agent.version or "1.0.0")

        await db.flush()
        return snapshot
    except SQLAlchemyError as e:
        logger.error(f"创建 AgentVersion 快照失败: {e}", exc_info=True)
        raise


async def list_versions(db: AsyncSession, agent_id: str) -> list[AgentVersion]:
    """列出版本历史（最新在前）。"""
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.created_at.desc())
    )
    return list(result.scalars().all())


async def get_version(
    db: AsyncSession, agent_id: str, version_id: str
) -> Optional[AgentVersion]:
    """获取指定版本快照。"""
    result = await db.execute(
        select(AgentVersion).where(
            AgentVersion.id == version_id,
            AgentVersion.agent_id == agent_id,
        )
    )
    return result.scalar_one_or_none()


async def rollback_to_version(
    db: AsyncSession,
    agent: Agent,
    version_id: str,
    user_id: Optional[str] = None,
) -> AgentVersion:
    """将 Agent 配置回滚到指定历史版本。

    流程：
    1. 读取目标版本快照
    2. 先把"当前"配置保存为新的快照（便于追溯回滚动作）
    3. 从目标快照恢复 agent 字段
    4. 递增 agent.version，新快照标记为 is_active=True
    """
    target = await get_version(db, agent.id, version_id)
    if not target:
        raise ValueError("version_not_found")

    snapshot_data = target.config_snapshot or {}

    # 1. 先保存当前配置为快照（changelog 标注回滚目标）
    rollback_changelog = f"回滚前快照（回滚到 v{target.version}）"
    pre_snapshot = await create_version_snapshot(db, agent, user_id, changelog=rollback_changelog)

    # 2. 从目标快照恢复字段
    agent.name = snapshot_data.get("name", agent.name)
    agent.description = snapshot_data.get("description", agent.description)
    agent.system_prompt = snapshot_data.get("system_prompt", agent.system_prompt)
    agent.config = snapshot_data.get("config", agent.config or {})

    await db.flush()
    return pre_snapshot


# ============================================================
# D4 6.5: 知识库版本管理与回滚
# ============================================================

def _build_knowledge_snapshot(agent: Agent, files: list[File]) -> dict:
    """构建知识库快照数据。

    结构：
    - file_list: 当前 Agent 关联的文件列表（path/hash/size/type/status）
    - vector_collection: 向量集合名（约定为 agent_{agent_id}）
    - chunk_strategy: 分块策略（从 agent.config 提取，缺失用默认值）
    - rag_config: RAG 配置（top_k/similarity_threshold/reranker，从 agent.config 提取）
    """
    config = agent.config or {}
    return {
        "file_list": [
            {
                "path": f.file_path,
                "hash": f.content_hash,
                "size": f.file_size,
                "file_type": f.file_type,
                "status": f.status,
                "chunk_count": f.chunk_count,
            }
            for f in files
        ],
        "vector_collection": f"agent_{agent.id}",
        "chunk_strategy": {
            "chunk_size": config.get("chunk_size", 1000),
            "chunk_overlap": config.get("chunk_overlap", 200),
            "strategy": config.get("chunk_strategy", "fixed"),
        },
        "rag_config": {
            "top_k": config.get("top_k", 5),
            "n_results": config.get("n_results", 5),
            "similarity_threshold": config.get("similarity_threshold", 0.7),
            "reranker": config.get("reranker"),
            "reranker_model": config.get("reranker_model"),
        },
    }


async def create_knowledge_snapshot(
    db: AsyncSession, agent_id: str
) -> Optional[AgentVersion]:
    """D4 6.5: 在知识库变更前持久化知识库状态快照。

    D3 的 incremental_updater._create_knowledge_snapshot_hook 在增量更新前调用本函数，
    记录当前知识库的文件列表 / 向量集合名 / 分块策略 / RAG 配置，支撑后续回滚。

    签名约束（D3 依赖）：async def create_knowledge_snapshot(db, agent_id) -> None
    - 失败时抛异常，D3 的钩子会捕获并降级（不阻塞增量更新）
    - 创建的快照 is_active=False（这是知识库审计快照，不是配置版本）
    - 显式 commit（项目硬约束：service 层写操作必须显式 commit）

    Args:
        db: 异步数据库 session
        agent_id: Agent ID

    Returns:
        创建的 AgentVersion 记录；Agent 不存在时返回 None
    """
    # 1. 查询 Agent
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        logger.warning(f"create_knowledge_snapshot: Agent 不存在（agent_id={agent_id}）")
        return None

    # 2. 查询当前文件列表
    file_result = await db.execute(
        select(File).where(File.agent_id == agent_id).order_by(File.created_at)
    )
    files = list(file_result.scalars().all())

    # 3. 构建知识库快照
    knowledge_snapshot = _build_knowledge_snapshot(agent, files)

    # 4. 创建 AgentVersion 记录
    #    版本号：基于 agent.version + "-kb-N" 后缀，标识这是知识库快照（非配置版本）
    #    is_active=False：知识库快照不参与配置版本激活态管理
    #    config_snapshot：留空 dict（与配置快照区分；nullable=False 故用空 dict）
    snapshot = AgentVersion(
        agent_id=agent_id,
        version=f"{agent.version or '1.0.0'}-kb",
        config_snapshot={},
        knowledge_snapshot=knowledge_snapshot,
        changelog=f"知识库快照：{len(files)} 个文件（增量更新前）",
        is_active=False,
        user_id=None,
    )
    db.add(snapshot)

    # 显式 commit：项目硬约束 — service 层写操作必须 await db.commit()
    await db.commit()
    await db.refresh(snapshot)

    logger.info(
        f"已创建知识库快照（agent={agent_id}, files={len(files)}, version={snapshot.version}）"
    )
    return snapshot


async def rollback_knowledge(
    db: AsyncSession,
    agent: Agent,
    version_id: str,
    user_id: Optional[str] = None,
) -> Agent:
    """D4 6.5: 将 Agent 知识库配置回滚到指定历史快照。

    D3 的 POST /agents/{agent_id}/rollback-knowledge/{version_id} 路由调用本函数。

    签名约束（D3 依赖）：
        async def rollback_knowledge(db, agent: Agent, version_id: str, user_id: str) -> Agent
    - 接收 Agent 对象（非 agent_id），D3 的路由会先取出 Agent
    - version_id 不存在时抛 ValueError("version_not_found")（D3 转 404）
    - 其他错误抛 ValueError 或 SQLAlchemyError（D3 转 400/500）
    - 显式 commit 后返回更新后的 agent（D3 会 refresh + 序列化）

    回滚范围（与配置回滚 rollback_to_version 区分）：
    - 这是审计快照，不实际删除/恢复文件物理实体
    - 仅恢复 agent.config 中的知识库相关参数（chunk_strategy / rag_config / vector_collection）
    - 创建一条新的 AgentVersion 记录标记此次回滚动作

    Args:
        db: 异步数据库 session
        agent: Agent 对象（D3 已校验并取出）
        version_id: 目标版本快照 ID
        user_id: 操作者 ID（用于审计）

    Returns:
        更新后的 Agent 对象

    Raises:
        ValueError("version_not_found"): 目标版本不存在或不属于该 Agent
        ValueError("knowledge_snapshot_missing"): 目标版本无知识库快照数据
    """
    # 1. 查找目标版本
    target = await get_version(db, agent.id, version_id)
    if not target:
        raise ValueError("version_not_found")

    knowledge_snap = target.knowledge_snapshot
    if not knowledge_snap:
        raise ValueError("knowledge_snapshot_missing")

    # 2. 从快照恢复 agent.config 中的知识库相关参数
    #    注意：不实际删除/恢复文件物理实体（这是审计快照）
    config = dict(agent.config or {})

    chunk_strategy = knowledge_snap.get("chunk_strategy") or {}
    if chunk_strategy:
        if "chunk_size" in chunk_strategy:
            config["chunk_size"] = chunk_strategy["chunk_size"]
        if "chunk_overlap" in chunk_strategy:
            config["chunk_overlap"] = chunk_strategy["chunk_overlap"]
        if "strategy" in chunk_strategy:
            config["chunk_strategy"] = chunk_strategy["strategy"]

    rag_config = knowledge_snap.get("rag_config") or {}
    if rag_config:
        if "top_k" in rag_config:
            config["top_k"] = rag_config["top_k"]
        if "n_results" in rag_config:
            config["n_results"] = rag_config["n_results"]
        if "similarity_threshold" in rag_config:
            config["similarity_threshold"] = rag_config["similarity_threshold"]
        if "reranker" in rag_config:
            config["reranker"] = rag_config["reranker"]
        if "reranker_model" in rag_config:
            config["reranker_model"] = rag_config["reranker_model"]

    agent.config = config

    # 3. 创建回滚记录（标记此次回滚动作，便于审计追溯）
    #    is_active=False：知识库回滚记录不参与配置版本激活态
    file_count = len(knowledge_snap.get("file_list") or [])
    rollback_record = AgentVersion(
        agent_id=agent.id,
        version=f"{agent.version or '1.0.0'}-rb",
        config_snapshot={
            "name": agent.name,
            "description": agent.description,
            "system_prompt": agent.system_prompt,
            "config": config,
            "version": agent.version,
        },
        knowledge_snapshot=knowledge_snap,
        changelog=f"回滚知识库到快照 v{target.version}（{file_count} 个文件）",
        is_active=False,
        user_id=user_id,
    )
    db.add(rollback_record)

    # 显式 commit：项目硬约束 — service 层写操作必须 await db.commit()
    await db.commit()
    await db.refresh(agent)

    logger.info(
        f"已回滚知识库（agent={agent.id}, target_version={target.version}, "
        f"rollback_record={rollback_record.id}）"
    )
    return agent


# ============================================================
# WT2: Runtime 版本管理扩展（Agent 配置一并回滚支持）
# ============================================================
# 本节为 v3 重构新增（PRD §4.6.3：回滚 Runtime 时 Agent 配置一并回滚）。
# 仅加性追加，不修改上方任何现有函数。复用现有 create_version_snapshot 为
# 企业内所有 Agent 创建回滚前快照，记录 Agent 配置在某个 Runtime 版本下的状态，
# 供后续 Agent 级回滚追溯。本函数由 services/runtime/runtime_version.rollback_to_version
# 调用，不在此处 commit（由调用方统一 commit）。


async def create_runtime_agent_snapshots(
    db: AsyncSession,
    enterprise_id: str,
    runtime_version_id: str,
    user_id: Optional[str] = None,
    changelog: Optional[str] = None,
) -> dict[str, str]:
    """为企业内所有 Agent 创建版本快照，记录其在某 Runtime 版本下的配置状态。

    在 Runtime 回滚前调用，捕获每个 Agent 当前的配置作为回滚点，支撑
    "Agent 配置一并回滚"的追溯能力（PRD §4.6.3）。

    复用现有 ``create_version_snapshot``（仅 flush，不 commit），由调用方统一 commit。
    单个 Agent 快照失败不影响其他 Agent（best-effort），失败项记入返回 manifest 的
    ``__errors__`` 字段。

    Args:
        db: 异步 session（与 Runtime 回滚同事务）
        enterprise_id: 企业 ID
        runtime_version_id: 关联的 Runtime 版本 ID（记入 changelog 便于追溯）
        user_id: 操作者
        changelog: 快照说明

    Returns:
        manifest: {agent_id: version_id}；失败项汇总在 ``__errors__`` 字段
    """
    manifest: dict[str, str] = {}
    errors: list[dict[str, str]] = []

    agents_result = await db.execute(
        select(Agent).where(Agent.enterprise_id == enterprise_id)
    )
    agents = list(agents_result.scalars().all())

    snapshot_note = changelog or f"Runtime 版本 {runtime_version_id} 关联的 Agent 快照"
    for agent in agents:
        try:
            snapshot = await create_version_snapshot(
                db, agent, user_id, changelog=snapshot_note
            )
            manifest[agent.id] = snapshot.id
        except Exception as e:  # noqa: BLE001 — 单个 Agent 失败不阻断整体快照
            logger.warning(
                "Runtime 回滚前为 Agent %s 创建快照失败: %s",
                agent.id,
                e,
                exc_info=True,
            )
            errors.append({"agent_id": agent.id, "error": str(e)})

    if errors:
        manifest["__errors__"] = json.dumps(errors, ensure_ascii=False)  # type: ignore[assignment]
    logger.info(
        "已创建 Runtime-Agent 快照（enterprise=%s, runtime_version=%s, agents=%d, failures=%d）",
        enterprise_id,
        runtime_version_id,
        len(manifest) - (1 if "__errors__" in manifest else 0),
        len(errors),
    )
    return manifest

