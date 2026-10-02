"""认知层 API 路由（spec.md §10.7 WT1 部分）。

端点清单（无尾斜杠，路径遵循 spec.md §10.7 契约）：
- GET    /cognition/knowledge-graph/{enterprise_id}     知识图谱查询
- PUT    /cognition/knowledge-graph/{enterprise_id}     知识图谱更新
- GET    /cognition/profile/{enterprise_id}             企业画像查询
- POST   /cognition/profile/{enterprise_id}             企业画像生成
- GET    /cognition/operating-model/{enterprise_id}     运行模型查询
- POST   /cognition/operating-model/{enterprise_id}     运行模型构建
- GET    /cognition/vitals/{enterprise_id}              企业生命体征聚合（UI v4）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.cognition import (
    KnowledgeGraphResponse,
    KnowledgeGraphCreate,
    EnterpriseProfileResponse,
    OperatingModelResponse,
)
from app.utils.security import get_current_user
from app.utils.response import success_response
from app.utils.rate_limit import rate_limit_api
from app.utils.audit import log_audit
from app.utils.error_codes import ErrorCode
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
)
from app.services.cognition.enterprise_profiler import EnterpriseProfiler
from app.services.cognition.operating_model import OperatingModelBuilder
from app.services.cognition.vitals import collect_vitals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cognition", tags=["认知层"])


def _verify_enterprise_admin(current_user: User, enterprise_id: str) -> None:
    """校验企业管理员权限。"""
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id or current_user.role != "admin":
        raise HTTPException(status_code=403, detail=ErrorCode.FORBIDDEN)


def _verify_enterprise_access(current_user: User, enterprise_id: str) -> None:
    """校验企业成员访问权限（读操作，不要求 admin 角色）。

    - 系统超管（enterprise_id 为空）：放行
    - 企业成员（enterprise_id 匹配）：放行
    - 其他：403
    """
    if current_user.enterprise_id is None and current_user.role == "admin":
        return
    if current_user.enterprise_id != enterprise_id:
        raise HTTPException(status_code=403, detail=ErrorCode.ENTERPRISE_ACCESS_DENIED)


@router.get("/knowledge-graph/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_knowledge_graph(
    request: Request,
    enterprise_id: str,
    node_type: str = Query(None, description="按实体类型过滤"),
    limit: int = Query(100, ge=1, le=500, description="节点数量上限"),
    offset: int = Query(0, ge=0, description="节点偏移量"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """知识图谱查询（PRD §4.2 数据结构定义 1）。"""
    _verify_enterprise_access(current_user, enterprise_id)

    graph_store = PGJSONBGraphStore(db, enterprise_id)
    if node_type:
        nodes = await graph_store.query_nodes_by_type(node_type)
        nodes = nodes[offset:offset + limit]
        edges = []
    else:
        all_nodes, all_edges = await graph_store.get_all()
        nodes = all_nodes[offset:offset + limit]
        node_ids = {n.node_id for n in nodes}
        edges = [e for e in all_edges if e.source_id in node_ids and e.target_id in node_ids]

    return success_response(data=KnowledgeGraphResponse(
        enterprise_id=enterprise_id,
        nodes=[n.model_dump() for n in nodes],
        edges=[e.model_dump() for e in edges],
    ).model_dump(mode="json"))


@router.put("/knowledge-graph/{enterprise_id}", response_model=None)
@rate_limit_api()
async def update_knowledge_graph(
    request: Request,
    enterprise_id: str,
    data: KnowledgeGraphCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """知识图谱更新（增量添加节点和边）。"""
    _verify_enterprise_admin(current_user, enterprise_id)

    graph_store = PGJSONBGraphStore(db, enterprise_id)
    for node in data.nodes:
        await graph_store.add_node(node)
    for edge in data.edges:
        await graph_store.add_edge(edge)
    await graph_store.save()

    node_count = await graph_store.count_nodes()

    await log_audit(
        db, current_user, "update_graph", "enterprise", enterprise_id,
        request=request,
        details={"added_nodes": len(data.nodes), "added_edges": len(data.edges), "total_nodes": node_count},
    )

    return success_response(
        data={"node_count": node_count, "edge_count": len(data.edges)},
        message="知识图谱已更新",
    )


@router.get("/profile/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_enterprise_profile(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """企业画像查询（PRD §4.2 数据结构定义 2）。"""
    _verify_enterprise_access(current_user, enterprise_id)

    profiler = EnterpriseProfiler(db, enterprise_id)
    profile = await profiler.get_profile()
    if profile is None:
        return success_response(data=None, message="尚未生成企业画像")

    return success_response(data=EnterpriseProfileResponse(
        enterprise_id=enterprise_id,
        profile=profile,
    ).model_dump(mode="json"))


@router.post("/profile/{enterprise_id}", response_model=None)
@rate_limit_api()
async def generate_enterprise_profile(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """企业画像生成（从知识图谱提取）。"""
    _verify_enterprise_admin(current_user, enterprise_id)

    profiler = EnterpriseProfiler(db, enterprise_id)
    profile = await profiler.generate_profile()
    await profiler.save_profile(profile)

    await log_audit(
        db, current_user, "generate_profile", "enterprise", enterprise_id,
        request=request, details={"version": profile.version},
    )

    return success_response(
        data=EnterpriseProfileResponse(
            enterprise_id=enterprise_id,
            profile=profile,
        ).model_dump(mode="json"),
        message="企业画像已生成",
    )


@router.get("/operating-model/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_operating_model(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行模型查询（PRD §4.2 数据结构定义 3）。"""
    _verify_enterprise_access(current_user, enterprise_id)

    builder = OperatingModelBuilder(db, enterprise_id)
    model = await builder.get_active_model()
    if model is None:
        return success_response(data=None, message="尚未构建运行模型")

    return success_response(data=OperatingModelResponse(
        enterprise_id=enterprise_id,
        model=model,
        version=model.version,
    ).model_dump(mode="json"))


@router.post("/operating-model/{enterprise_id}", response_model=None)
@rate_limit_api()
async def build_operating_model(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行模型构建（从知识图谱提取 6 大块）。"""
    _verify_enterprise_admin(current_user, enterprise_id)

    builder = OperatingModelBuilder(db, enterprise_id)
    model = await builder.build_model()
    await builder.save_model(model)

    await log_audit(
        db, current_user, "build_model", "enterprise", enterprise_id,
        request=request, details={"version": model.version, "completeness": model.completeness},
    )

    return success_response(
        data=OperatingModelResponse(
            enterprise_id=enterprise_id,
            model=model,
            version=model.version,
        ).model_dump(mode="json"),
        message="运行模型已构建",
    )


@router.get("/vitals/{enterprise_id}", response_model=None)
@rate_limit_api()
async def get_enterprise_vitals(
    request: Request,
    enterprise_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """企业生命体征聚合（UI v4 §七 P1-6）。

    驾驶舱首屏一次拿全运转状态，替代此前并发 6 个接口再前端拼装的做法。
    返回：员工编制与在岗、事件流速率、待审批、影子信任度、编译状态、健康度。

    所有数值均为真实统计 —— 没有运转的企业就会得到平直的心电线，
    这是本次重构刻意保留的诚实性（见 UI v4 §一 罪一）。
    """
    _verify_enterprise_access(current_user, enterprise_id)
    vitals = await collect_vitals(db, enterprise_id)
    return success_response(data=vitals)
