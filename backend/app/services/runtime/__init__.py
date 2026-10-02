"""Enterprise Runtime 服务子包（WT2）。

实现 PRD §4.6（Enterprise Runtime 数据结构）与 §4.6.3（版本管理）的业务层。

子模块：
- ``runtime_store``：Runtime 持久化（保存/查询/激活态管理）
- ``runtime_version``：版本管理（快照/回滚/diff，语义化版本号）
- ``runtime_query``：Runtime 查询接口（spec §10.5，供 WT3/WT4 消费）
"""
from app.services.runtime.runtime_query import (  # noqa: F401
    RuntimeQueryService,
    get_active_runtime,
    get_agent_template,
    get_agent_templates,
    get_collaboration_graph,
    get_organization,
    get_process_engines,
    get_runtime_version,
    get_tool_registry,
)
from app.services.runtime.runtime_store import (  # noqa: F401
    bump_semver,
    get_active_runtime as store_get_active_runtime,
    get_runtime_by_version,
    list_versions,
    save_runtime,
)
from app.services.runtime.runtime_version import (  # noqa: F401
    create_version_snapshot,
    diff_versions,
    rollback_to_version,
)

__all__ = [
    # store
    "save_runtime",
    "store_get_active_runtime",
    "get_runtime_by_version",
    "list_versions",
    "bump_semver",
    # version
    "create_version_snapshot",
    "rollback_to_version",
    "diff_versions",
    # query
    "RuntimeQueryService",
    "get_active_runtime",
    "get_organization",
    "get_agent_templates",
    "get_agent_template",
    "get_process_engines",
    "get_collaboration_graph",
    "get_tool_registry",
    "get_runtime_version",
]
