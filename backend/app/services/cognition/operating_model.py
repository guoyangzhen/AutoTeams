"""企业运行模型建模服务（PRD §4.2 数据结构定义 3）。

建模企业运行模型（6 大块）：organization/roles/processes/capabilities/runtime_rules/gaps。
输出 EnterpriseOperatingModel schema（设计态）。
版本管理接口（与 WT2 协作，由 WT2 实现持久化）。
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cognition import EnterpriseOperatingModel
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
    RelationType,
)
from app.schemas.cognition import (
    EnterpriseOperatingModelData,
    RoleDefinition,
    ProcessDefinitionModel,
    CapabilityItem,
    RuntimeRules,
    GapItem,
)

logger = logging.getLogger(__name__)


class OperatingModelBuilder:
    """企业运行模型构建器。

    从知识图谱提取组织/角色/流程/权限/KPI 及其运行关系，
    形成可审视、可调整、可版本管理的设计态定义。
    """

    def __init__(self, db: AsyncSession, enterprise_id: str):
        self._db = db
        self._enterprise_id = enterprise_id

    async def build_model(self) -> EnterpriseOperatingModelData:
        """从知识图谱构建运行模型。"""
        graph_store = PGJSONBGraphStore(self._db, self._enterprise_id)
        departments = await graph_store.query_nodes_by_type(EntityType.DEPARTMENT.value)
        roles = await graph_store.query_nodes_by_type(EntityType.ROLE.value)
        processes = await graph_store.query_nodes_by_type(EntityType.PROCESS.value)
        permissions = await graph_store.query_nodes_by_type(EntityType.PERMISSION.value)
        kpis = await graph_store.query_nodes_by_type(EntityType.KPI.value)
        systems = await graph_store.query_nodes_by_type(EntityType.SYSTEM.value)

        # 构建组织架构
        organization = self._build_organization(departments, roles)

        # 构建岗位定义
        role_defs = await self._build_roles(graph_store, roles, permissions, kpis)

        # 构建流程定义
        process_defs = await self._build_processes(graph_store, processes, systems)

        # 构建能力项
        capabilities = await self._build_capabilities(graph_store, roles, processes)

        # 构建运行规则
        runtime_rules = self._build_runtime_rules(roles, processes)

        # 识别空白
        gaps = self._identify_gaps(departments, roles, processes)

        model = EnterpriseOperatingModelData(
            version="v1.0.0",
            completeness=0.0,
            organization=organization,
            roles=role_defs,
            processes=process_defs,
            capabilities=capabilities,
            runtime_rules=runtime_rules,
            gaps=gaps,
        )
        return model

    def _build_organization(self, departments, roles) -> dict:
        """构建组织架构。"""
        dept_list = []
        for d in departments:
            dept_list.append({
                "id": d.node_id,
                "name": d.name,
                "parent_id": d.attributes.get("parent_id"),
                "responsibilities": d.attributes.get("responsibilities", []),
            })
        hierarchy_tree = self._build_hierarchy(dept_list)
        return {
            "departments": dept_list,
            "hierarchy_tree": hierarchy_tree,
            "reporting_lines": [],
        }

    def _build_hierarchy(self, depts: list[dict]) -> dict:
        """构建部门层级树。"""
        root_depts = [d for d in depts if not d.get("parent_id")]
        tree: dict = {}
        for root in root_depts:
            tree[root["id"]] = self._build_subtree(root["id"], depts)
        return tree

    def _build_subtree(self, parent_id: str, depts: list[dict]) -> dict:
        """递归构建子树。"""
        children = [d for d in depts if d.get("parent_id") == parent_id]
        return {
            "name": next((d["name"] for d in depts if d["id"] == parent_id), ""),
            "children": {c["id"]: self._build_subtree(c["id"], depts) for c in children},
        }

    async def _build_roles(
        self, graph_store, roles, permissions, kpis
    ) -> list[RoleDefinition]:
        """构建岗位定义。"""
        role_defs: list[RoleDefinition] = []
        for role in roles:
            # 查询岗位关联的权限和 KPI
            perm_neighbors = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.HAS_PERMISSION.value
            )
            kpi_neighbors = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.OWES.value
            )
            dept_neighbors = await graph_store.query_neighbors(
                role.node_id, relation=RelationType.BELONGS_TO.value
            )
            dept_name = dept_neighbors[0].name if dept_neighbors else ""
            role_defs.append(RoleDefinition(
                id=role.node_id,
                title=role.name,
                department=dept_name,
                level=role.attributes.get("level", ""),
                responsibilities=role.attributes.get("responsibilities", []),
                required_skills=role.attributes.get("required_skills", []),
                kpi_ids=[k.node_id for k in kpi_neighbors],
                permission_ids=[p.node_id for p in perm_neighbors],
            ))
        return role_defs

    async def _build_processes(
        self, graph_store, processes, systems
    ) -> list[ProcessDefinitionModel]:
        """构建流程定义。"""
        process_defs: list[ProcessDefinitionModel] = []
        for proc in processes:
            system_neighbors = await graph_store.query_neighbors(
                proc.node_id, relation=RelationType.USES.value
            )
            owner_neighbors = await graph_store.query_neighbors(
                proc.node_id, relation=RelationType.EXECUTES.value, direction="in"
            )
            process_defs.append(ProcessDefinitionModel(
                id=proc.node_id,
                name=proc.name,
                type=proc.attributes.get("type", "sop"),
                steps=proc.attributes.get("steps", []),
                owner_role_id=owner_neighbors[0].node_id if owner_neighbors else "",
                participants=proc.attributes.get("participants", []),
                trigger_event=proc.attributes.get("trigger_event", ""),
                system_ids=[s.node_id for s in system_neighbors],
            ))
        return process_defs

    async def _build_capabilities(
        self, graph_store, roles, processes
    ) -> list[CapabilityItem]:
        """构建岗位能力项。"""
        capabilities: list[CapabilityItem] = []
        for role in roles:
            capabilities.append(CapabilityItem(
                role_id=role.node_id,
                required_capabilities=role.attributes.get("required_skills", []),
                knowledge_sources=role.attributes.get("knowledge_sources", []),
                tools=role.attributes.get("tools", []),
            ))
        return capabilities

    def _build_runtime_rules(self, roles, processes) -> RuntimeRules:
        """构建运行规则。"""
        return RuntimeRules(
            collaboration_rules=[],
            data_flow_rules=[],
            escalation_rules=[],
        )

    def _identify_gaps(self, departments, roles, processes) -> list[GapItem]:
        """识别知识空白。"""
        gaps: list[GapItem] = []
        process_names = [p.name.lower() for p in processes]
        for cp in ["采购", "财务", "售后"]:
            if not any(cp in pn for pn in process_names):
                gaps.append(GapItem(
                    area=f"{cp}流程",
                    severity="medium",
                    suggestion=f"建议补充{cp}流程文档以提升完成度",
                ))
        return gaps

    async def save_model(self, model: EnterpriseOperatingModelData) -> EnterpriseOperatingModel:
        """保存运行模型到数据库。"""
        # 停用旧版本
        old_models = await self._db.execute(
            select(EnterpriseOperatingModel).where(
                EnterpriseOperatingModel.enterprise_id == self._enterprise_id,
                EnterpriseOperatingModel.is_active == True,  # noqa: E712
            )
        )
        for old in old_models.scalars():
            old.is_active = False

        record = EnterpriseOperatingModel(
            enterprise_id=self._enterprise_id,
            model=model.model_dump(mode="json"),
            version=model.version,
            completeness=int(model.completeness),
            is_active=True,
        )
        self._db.add(record)
        await self._db.commit()
        await self._db.refresh(record)
        return record

    async def get_active_model(self) -> Optional[EnterpriseOperatingModelData]:
        """获取当前激活的运行模型。"""
        result = await self._db.execute(
            select(EnterpriseOperatingModel).where(
                EnterpriseOperatingModel.enterprise_id == self._enterprise_id,
                EnterpriseOperatingModel.is_active == True,  # noqa: E712
            ).order_by(EnterpriseOperatingModel.created_at.desc()).limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return EnterpriseOperatingModelData(**record.model)
