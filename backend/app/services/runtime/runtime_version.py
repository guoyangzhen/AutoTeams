"""Runtime 版本管理（WT2）。

实现 PRD §4.6.3：创建快照 / 回滚 / diff，语义化版本号。

- ``create_version_snapshot``：为指定 Runtime 版本创建审计快照事件
- ``rollback_to_version``：回滚到任意历史版本（Agent 配置一并记录回滚点）
- ``diff_versions``：两版本结构化 diff + LLM 辅助生成人类可读摘要

工程约束：
- 写操作显式 ``await db.commit()``
- LLM 调用前用户输入经 ``prompt_security.wrap_untrusted`` 包裹（spec §2.6）
- LLM 失败时降级为确定性结构化摘要，不阻断 diff 接口
"""
import logging
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runtime import EnterpriseRuntime, RuntimeVersion
from app.schemas.runtime import (
    RuntimeDiffChange,
    RuntimeDiffResponse,
)
from app.services.runtime.runtime_store import (
    _cache_key,
    _write_audit_log,
    get_active_runtime,
    get_runtime_by_version,
)
from app.utils.cache import cache_delete

logger = logging.getLogger(__name__)


async def create_version_snapshot(
    db: AsyncSession,
    runtime_id: str,
    changelog: Optional[str] = None,
    created_by: Optional[str] = None,
) -> RuntimeVersion:
    """为指定 Runtime 版本创建一条审计快照事件。

    不改变激活态，仅记录"在某时刻对某版本做了快照"的事实，便于后续追溯。

    Args:
        db: 异步 session
        runtime_id: 目标 Runtime 版本 ID
        changelog: 变更说明
        created_by: 操作者

    Returns:
        创建的 RuntimeVersion 事件记录

    Raises:
        ValueError("runtime_not_found"): runtime_id 不存在
    """
    result = await db.execute(
        select(EnterpriseRuntime).where(EnterpriseRuntime.id == runtime_id)
    )
    runtime = result.scalar_one_or_none()
    if runtime is None:
        raise ValueError("runtime_not_found")

    event = RuntimeVersion(
        runtime_id=runtime.id,
        enterprise_id=runtime.enterprise_id,
        version=runtime.version,
        event_type="snapshot",
        changelog=changelog or f"快照 Runtime 版本 {runtime.version}",
        is_active=runtime.is_active,
        created_by=created_by,
        metadata_json={"snapshot_of": runtime.version},
    )
    db.add(event)

    # 写 HMAC 链式签名审计日志（spec §2.6）
    await _write_audit_log(
        db,
        created_by,
        action="runtime.snapshot",
        resource_id=runtime.id,
        details={
            "enterprise_id": runtime.enterprise_id,
            "version": runtime.version,
        },
    )

    await db.commit()
    await db.refresh(event)
    logger.info(
        "已创建 Runtime 快照事件（runtime=%s, version=%s）",
        runtime.id,
        runtime.version,
    )
    return event


async def rollback_to_version(
    db: AsyncSession,
    enterprise_id: str,
    target_version: str,
    created_by: Optional[str] = None,
    snapshot_agents: bool = True,
) -> EnterpriseRuntime:
    """回滚到指定历史 Runtime 版本（PRD §4.6.3：Agent 配置一并回滚）。

    流程：
    1. 定位目标版本（须存在且属于该企业）
    2. 若目标已是激活版本，直接返回（幂等）
    3. （可选）为当前所有 Agent 创建版本快照，记录回滚前的 Agent 配置状态
    4. 将当前激活版本置为 inactive，目标版本置为 active
    5. 同步 Enterprise.current_runtime_version_id
    6. 写 RuntimeVersion 回滚事件
    7. 失效缓存 + 显式 commit

    Args:
        db: 异步 session
        enterprise_id: 企业 ID
        target_version: 目标版本号（带或不带 "v" 前缀均可）
        created_by: 操作者
        snapshot_agents: 是否在回滚前为 Agent 创建快照（默认 True）

    Returns:
        回滚后激活的 EnterpriseRuntime（即目标版本）

    Raises:
        ValueError("runtime_version_not_found"): 目标版本不存在
    """
    target = await get_runtime_by_version(db, enterprise_id, target_version)
    if target is None:
        raise ValueError("runtime_version_not_found")

    # 幂等：目标已是激活版本
    if target.is_active:
        logger.info(
            "目标版本已是激活版本，回滚为 no-op（enterprise=%s, version=%s）",
            enterprise_id,
            target.version,
        )
        return target

    current_active = await get_active_runtime(db, enterprise_id, use_cache=False)
    from_version = current_active.version if current_active else None

    # 3. 回滚前为 Agent 创建快照（best-effort，不阻断回滚）
    agent_snapshot_manifest: dict[str, Any] = {}
    if snapshot_agents:
        try:
            # 局部导入避免循环依赖
            from app.services.agent_version_service import create_runtime_agent_snapshots

            agent_snapshot_manifest = await create_runtime_agent_snapshots(
                db,
                enterprise_id=enterprise_id,
                runtime_version_id=target.id,
                user_id=created_by,
                changelog=f"Runtime 回滚前快照（{from_version} → {target.version}）",
            )
        except Exception as e:  # noqa: BLE001 — 桥接层失败不应阻断 Runtime 回滚
            logger.warning("Runtime 回滚前 Agent 快照失败（非致命）: %s", e, exc_info=True)
            agent_snapshot_manifest = {"error": str(e)}

    try:
        # 4. 切换激活态
        await db.execute(
            update(EnterpriseRuntime)
            .where(
                EnterpriseRuntime.enterprise_id == enterprise_id,
                EnterpriseRuntime.is_active.is_(True),
            )
            .values(is_active=False)
        )
        await db.execute(
            update(EnterpriseRuntime)
            .where(EnterpriseRuntime.id == target.id)
            .values(is_active=True)
        )

        # 5. 同步 Enterprise.current_runtime_version_id
        from app.models.enterprise import Enterprise

        await db.execute(
            update(Enterprise)
            .where(Enterprise.id == enterprise_id)
            .values(current_runtime_version_id=target.id)
        )

        # 6. 写回滚事件
        event = RuntimeVersion(
            runtime_id=target.id,
            enterprise_id=enterprise_id,
            version=target.version,
            event_type="rollback",
            changelog=f"回滚 Runtime：{from_version} → {target.version}",
            is_active=True,
            created_by=created_by,
            metadata_json={
                "from_version": from_version,
                "to_version": target.version,
                "agent_snapshot_count": len(agent_snapshot_manifest)
                if isinstance(agent_snapshot_manifest, dict)
                else 0,
            },
        )
        db.add(event)

        # 6.1 写 HMAC 链式签名审计日志（spec §2.6）
        await _write_audit_log(
            db,
            created_by,
            action="runtime.rollback",
            resource_id=target.id,
            details={
                "enterprise_id": enterprise_id,
                "from_version": from_version,
                "to_version": target.version,
            },
        )

        # 7. 失效缓存 + 显式 commit
        cache_delete(_cache_key(enterprise_id))
        await db.commit()
        await db.refresh(target)

        logger.info(
            "已回滚 Runtime（enterprise=%s, %s → %s）",
            enterprise_id,
            from_version,
            target.version,
        )
        return target
    except SQLAlchemyError as e:
        logger.error("Runtime 回滚失败: %s", e, exc_info=True)
        raise


# ============================================================
# diff
# ============================================================


def _index_by_key(items: list[dict], key: str) -> dict[str, dict]:
    """将 dict 列表按 key 字段索引。"""
    return {
        str(item.get(key, idx)): item for idx, item in enumerate(items) if isinstance(item, dict)
    }


def _diff_list_section(
    section: str,
    list_a: list,
    list_b: list,
    key_field: str,
) -> list[RuntimeDiffChange]:
    """对比两个列表（按 key_field 索引），产出 added/removed/modified 变更项。"""
    changes: list[RuntimeDiffChange] = []
    index_a = _index_by_key(list_a, key_field)
    index_b = _index_by_key(list_b, key_field)

    for k, b_item in index_b.items():
        if k not in index_a:
            changes.append(
                RuntimeDiffChange(section=section, change_type="added", key=k)
            )
        elif index_a[k] != b_item:
            changes.append(
                RuntimeDiffChange(section=section, change_type="modified", key=k)
            )
    for k in index_a:
        if k not in index_b:
            changes.append(
                RuntimeDiffChange(section=section, change_type="removed", key=k)
            )
    return changes


def _build_structured_changes(
    data_a: dict, data_b: dict
) -> list[RuntimeDiffChange]:
    """构造两份 runtime_data 的结构化差异清单。"""
    changes: list[RuntimeDiffChange] = []

    # agents（按 agent_id）
    changes.extend(
        _diff_list_section("agents", data_a.get("agents", []), data_b.get("agents", []), "agent_id")
    )
    # process_engines（按 engine_id）
    changes.extend(
        _diff_list_section(
            "process_engines",
            data_a.get("process_engines", []),
            data_b.get("process_engines", []),
            "engine_id",
        )
    )
    # tool_registry（按 tool_id）
    changes.extend(
        _diff_list_section(
            "tool_registry",
            data_a.get("tool_registry", []),
            data_b.get("tool_registry", []),
            "tool_id",
        )
    )
    # organization.departments（按 dept_id）
    org_a = data_a.get("organization") or {}
    org_b = data_b.get("organization") or {}
    changes.extend(
        _diff_list_section(
            "organization.departments",
            org_a.get("departments", []),
            org_b.get("departments", []),
            "dept_id",
        )
    )
    # collaboration_graph.edges（按 source_id+target_id+relation 组合键）
    edges_a = (data_a.get("collaboration_graph") or {}).get("edges", []) or []
    edges_b = (data_b.get("collaboration_graph") or {}).get("edges", []) or []
    edge_key = lambda e: f"{e.get('source_id','')}->{e.get('target_id','')}:{e.get('relation','')}"  # noqa: E731
    index_a = {edge_key(e): e for e in edges_a if isinstance(e, dict)}
    index_b = {edge_key(e): e for e in edges_b if isinstance(e, dict)}
    for k, b_item in index_b.items():
        if k not in index_a:
            changes.append(RuntimeDiffChange(section="collaboration_graph.edges", change_type="added", key=k))
        elif index_a[k] != b_item:
            changes.append(RuntimeDiffChange(section="collaboration_graph.edges", change_type="modified", key=k))
    for k in index_a:
        if k not in index_b:
            changes.append(RuntimeDiffChange(section="collaboration_graph.edges", change_type="removed", key=k))

    # 顶层标量字段
    for scalar in ("completeness", "model_version"):
        if data_a.get(scalar) != data_b.get(scalar):
            changes.append(
                RuntimeDiffChange(
                    section=scalar,
                    change_type="modified",
                    key=scalar,
                    detail=f"{data_a.get(scalar)} → {data_b.get(scalar)}",
                )
            )

    return changes


def _fallback_summary(
    version_a: str, version_b: str, changes: list[RuntimeDiffChange]
) -> str:
    """LLM 不可用时的确定性摘要。"""
    if not changes:
        return f"版本 {version_a} 与 {version_b} 无差异。"
    by_section: dict[str, list[RuntimeDiffChange]] = {}
    for c in changes:
        by_section.setdefault(c.section, []).append(c)
    parts = [f"版本 {version_a} → {version_b} 共 {len(changes)} 处变更："]
    for section, items in by_section.items():
        added = sum(1 for i in items if i.change_type == "added")
        removed = sum(1 for i in items if i.change_type == "removed")
        modified = sum(1 for i in items if i.change_type == "modified")
        parts.append(
            f"- {section}：新增 {added} / 移除 {removed} / 修改 {modified}"
        )
    return "\n".join(parts)


async def _llm_diff_summary(
    version_a: str, version_b: str, changes: list[RuntimeDiffChange]
) -> Optional[str]:
    """调用 LLM 生成人类可读 diff 摘要（失败返回 None）。"""
    try:
        from app.services.llm_service import ModelTier, llm_service
        from app.services.prompt_security import wrap_untrusted
    except ImportError:  # pragma: no cover — 依赖缺失时降级
        return None

    if not changes:
        return None

    # 结构化变更清单（不含敏感原文，仅 key 与类型）
    change_lines = [
        f"- [{c.change_type}] {c.section}: {c.key}" + (f" ({c.detail})" if c.detail else "")
        for c in changes
    ]
    structured = "\n".join(change_lines)
    # 用 wrap_untrusted 包裹结构化数据（spec §2.6：用户/外部输入进入 LLM 前需包裹）
    safe_input = wrap_untrusted(structured, label="Runtime 版本差异结构化数据")

    messages = [
        {
            "role": "system",
            "content": (
                "你是企业 AI 组织分析助手。请根据两份 Enterprise Runtime 版本之间的结构化差异，"
                "生成一段简洁的中文人类可读摘要，说明主要变化与潜在影响。不要复述原始 JSON，"
                "用自然语言总结。不超过 200 字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"版本对比：{version_a} → {version_b}\n"
                f"结构化差异：\n{safe_input}\n"
                "请生成摘要。"
            ),
        },
    ]
    try:
        text = await llm_service.chat(messages, tier=ModelTier.CHEAP, temperature=0.3, max_tokens=512)
        text = (text or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001 — LLM 失败降级
        logger.warning("LLM 生成 diff 摘要失败，降级为结构化摘要: %s", e, exc_info=True)
        return None


async def diff_versions(
    db: AsyncSession,
    enterprise_id: str,
    version_a: str,
    version_b: str,
    use_llm: bool = True,
) -> RuntimeDiffResponse:
    """对比两个 Runtime 版本，返回结构化差异 + 人类可读摘要。

    Args:
        db: 异步 session
        enterprise_id: 企业 ID
        version_a: 起始版本号
        version_b: 目标版本号
        use_llm: 是否调用 LLM 生成摘要（测试可关闭）

    Returns:
        RuntimeDiffResponse

    Raises:
        ValueError("runtime_version_not_found"): 任一版本不存在
    """
    runtime_a = await get_runtime_by_version(db, enterprise_id, version_a)
    runtime_b = await get_runtime_by_version(db, enterprise_id, version_b)
    if runtime_a is None or runtime_b is None:
        raise ValueError("runtime_version_not_found")

    data_a = runtime_a.runtime_data or {}
    data_b = runtime_b.runtime_data or {}
    changes = _build_structured_changes(data_a, data_b)

    summary = _fallback_summary(runtime_a.version, runtime_b.version, changes)
    if use_llm:
        llm_summary = await _llm_diff_summary(runtime_a.version, runtime_b.version, changes)
        if llm_summary:
            summary = llm_summary

    return RuntimeDiffResponse(
        version_a=runtime_a.version,
        version_b=runtime_b.version,
        changes=changes,
        summary=summary,
        a_compiled_at=runtime_a.compiled_at,
        b_compiled_at=runtime_b.compiled_at,
    )
