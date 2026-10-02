"""API 路由按业务分域聚合。

原先 36 个 router 全部平铺挂在 ``app/main.py``，新增一个业务域就要改一次入口文件。
本模块把它们收敛为 5 个业务域，``api_router`` 统一挂载在 ``/api/v1`` 前缀下：

======================  ==========================================================
业务域                  端点前缀
======================  ==========================================================
``auth``      认证      ``/auth``
``workspace`` 工作台    ``/enterprises`` ``/folders`` ``/files`` ``/setup``
                       ``/conversations`` ``/process`` ``/loop`` ``/flow-core`` 等
``workforce`` 数字员工  ``/agents`` ``/workforce`` ``/skills`` ``/memory`` 等
``knowledge`` 知识库    ``/cognition`` ``/compiler``
``system``    系统      ``/audit-logs`` ``/metrics`` ``/llm-config`` ``/shadow`` 等
======================  ==========================================================

.. warning::
   **不要给业务域追加 URL 前缀。** 所有历史端点路径已被前端与外部 Agent 依赖，
   域聚合只用于组织代码与 OpenAPI 标签，路径必须逐字保持不变。

.. warning::
   同一业务域内保持原有注册顺序，其中两处顺序敏感：
   ``copilot`` 必须先于 ``setup``（``/setup/copilot`` 不能被 ``/setup/{session_id}/*`` 覆盖），
   ``shadow`` 必须先于 ``counterfactual_shadow``。

``public_router`` 为不带 ``/api/v1`` 前缀的运维探活端点（``/health``、``/metrics``）。
"""
from fastapi import APIRouter

# ============================================================
# 导入各业务域的子路由
# ============================================================
# 业务域 1/5：认证
from app.api.auth import router as auth_router

# 业务域 2/5：工作台（企业、文件、对话、流程、自动化）
from app.api.enterprise import router as enterprise_router
from app.api.folders import router as folders_router
from app.api.files import router as files_router
from app.api.copilot import router as copilot_router
from app.api.setup import router as setup_router
from app.api.process import router as process_router
from app.api.conversations import router as conversations_router
from app.api.audio import router as audio_router
from app.api.loop import router as loop_router
from app.api.flow_core import router as flow_core_router
from app.api.local_paths import router as local_paths_router
from app.api.runtime import router as runtime_router
from app.api.evolution import router as evolution_router
from app.api.interview import router as interview_router
from app.api.collaboration import router as collaboration_router
from app.api.product_analytics import router as product_analytics_router

# 业务域 3/5：数字员工（员工、技能、记忆、协同团队、外部 Agent 接入）
from app.api.agents import router as agents_router
from app.api.skills import router as skills_router
from app.api.templates import router as templates_router
from app.api.workforce import router as workforce_router
from app.api.workforce_profiles import router as workforce_profiles_router
from app.api.cognitive_memory import router as cognitive_memory_router
from app.api.agent_product import router as agent_product_router
from app.api.mcp_evolution import router as mcp_evolution_router
from app.api.team_matrix import router as team_matrix_router
from app.api.connectors import router as connectors_router
from app.api.strike_teams import router as strike_teams_router
from app.api.external_agents import router as external_agents_router

# 业务域 4/5：知识库（认知层与五级编译器）
from app.api.cognition import router as cognition_router
from app.api.compiler import router as compiler_router

# 业务域 5/5：系统（审计、指标、模型配置、影子评估、本地执行器）
from app.api.system import public_router, router as system_router

# 兼容导出：这两个 router 已由 system_router 聚合，此处仅保留历史导入路径
from app.api.audit_logs import router as audit_logs_router
from app.api.metrics import router as metrics_router


# ============================================================
# 聚合
# ============================================================
def _build_api_router() -> APIRouter:
    """按业务域注册全部子路由，返回挂载在 /api/v1 下的聚合 router。"""
    router = APIRouter()
    domains: tuple[tuple[str, tuple[APIRouter, ...]], ...] = (
        # 业务域 1/5：认证
        ("认证", (auth_router,)),
        # 业务域 2/5：工作台（copilot 顺序敏感，必须先于 setup）
        (
            "工作台",
            (
                enterprise_router,
                folders_router,
                files_router,
                copilot_router,
                setup_router,
                process_router,
                conversations_router,
                audio_router,
                loop_router,
                flow_core_router,
                local_paths_router,
                runtime_router,
                evolution_router,
                interview_router,
                collaboration_router,
                product_analytics_router,
            ),
        ),
        # 业务域 3/5：数字员工
        (
            "数字员工",
            (
                agents_router,
                skills_router,
                templates_router,
                workforce_router,
                workforce_profiles_router,
                cognitive_memory_router,
                agent_product_router,
                mcp_evolution_router,
                team_matrix_router,
                connectors_router,
                strike_teams_router,
                external_agents_router,
            ),
        ),
        # 业务域 4/5：知识库
        ("知识库", (cognition_router, compiler_router)),
        # 业务域 5/5：系统
        ("系统", (system_router,)),
    )
    for tag, sub_routers in domains:
        for sub_router in sub_routers:
            router.include_router(sub_router, tags=[tag])
    return router


api_router = _build_api_router()

__all__ = [
    # 聚合路由
    "api_router",
    "public_router",
    # 兼容导出：各子路由仍可从 app.api 直接导入
    "auth_router",
    "enterprise_router",
    "folders_router",
    "files_router",
    "setup_router",
    "copilot_router",
    "process_router",
    "agents_router",
    "skills_router",
    "conversations_router",
    "loop_router",
    "audit_logs_router",
    "metrics_router",
    "templates_router",
    "cognition_router",
    "compiler_router",
    "audio_router",
    "local_paths_router",
    "agent_product_router",
    "external_agents_router",
    "system_router",
]
