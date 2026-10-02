"""agents 路由包：按职责拆分为 4 个子模块 (3.3.3)。

历史：本目录原为单文件 ``app/api/agents.py``（1375 行，16 端点，6 类职责）。
现按职责拆分为 ``crud`` / ``chat`` / ``build`` / ``knowledge`` 四个子模块，
跨模块共享的 Agent 查找助手移至 ``_helpers``。

向后兼容
--------
``app.api.agents.router`` 仍是聚合后的单一路由器（prefix="/agents"），
``app/api/__init__.py`` 的 ``from app.api.agents import router as agents_router``
无需改动。

注意（测试 monkeypatch）
------------------------
子模块通过 ``from app.api.agents._helpers import _get_agent_or_404`` 等方式引用
助手函数，因此各子模块拥有独立的命名空间绑定。测试 monkeypatch 应针对
**调用方所在子模块**的命名空间，例如：

- 回滚知识库端点 → ``app.api.agents.knowledge._get_agent_or_404``
- chat/stream 端点 → ``app.api.agents.chat._search_knowledge``
- 构建端点 → ``app.api.agents.build.async_session_factory``

而非 ``app.api.agents._get_agent_or_404``（该路径在拆分后不再持有运行时绑定）。
"""
from fastapi import APIRouter

from app.api.agents import crud, chat, build, knowledge

# 聚合路由器：子模块各自携带 prefix="/agents"，此处仅做聚合（无需再设前缀，
# 否则会与子模块前缀叠加）。app.api.agents.router 对外仍是 /agents 下的完整路由器。
router = APIRouter()
router.include_router(crud.router)
router.include_router(chat.router)
router.include_router(build.router)
router.include_router(knowledge.router)

__all__ = ["router"]
