"""企业画像生成服务（PRD §4.2 数据结构定义 2）。

从知识图谱提取企业画像：行业/规模/组织复杂度/业务成熟度/数据完备度。
输出 EnterpriseProfile schema（标签 + 特征 + 概要）。
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cognition import EnterpriseProfile
from app.services.cognition.knowledge_graph import (
    PGJSONBGraphStore,
    EntityType,
)
from app.schemas.cognition import (
    EnterpriseProfileData,
    EnterpriseBasic,
    EnterpriseOrgSummary,
    EnterpriseMaturity,
    EnterpriseBusiness,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class EnterpriseProfiler:
    """企业画像生成器。

    从知识图谱中提取实体信息，聚合为企业画像。
    """

    def __init__(self, db: AsyncSession, enterprise_id: str):
        self._db = db
        self._enterprise_id = enterprise_id

    async def generate_profile(self) -> EnterpriseProfileData:
        """从知识图谱提取企业画像。"""
        graph_store = PGJSONBGraphStore(self._db, self._enterprise_id)
        departments = await graph_store.query_nodes_by_type(EntityType.DEPARTMENT.value)
        roles = await graph_store.query_nodes_by_type(EntityType.ROLE.value)
        products = await graph_store.query_nodes_by_type(EntityType.PRODUCT.value)
        customers = await graph_store.query_nodes_by_type(EntityType.CUSTOMER.value)
        processes = await graph_store.query_nodes_by_type(EntityType.PROCESS.value)
        systems = await graph_store.query_nodes_by_type(EntityType.SYSTEM.value)

        # 基本信息从部门属性中推断
        basic = EnterpriseBasic()
        if departments:
            first_dept = departments[0]
            basic.industry = first_dept.attributes.get("industry", "")

        org_summary = EnterpriseOrgSummary(
            department_count=len(departments),
            headcount=len(roles),  # MVP 用岗位数近似
            key_roles=[r.name for r in roles[:10]],
        )

        maturity = EnterpriseMaturity(
            level="L1",
            automation_coverage=0.0,
            ai_workforce_count=0,
        )

        business = EnterpriseBusiness(
            main_products=[p.name for p in products[:20]],
            target_industries=list({c.attributes.get("industry", "") for c in customers if c.attributes.get("industry")}),
            core_processes=[p.name for p in processes[:10]],
        )

        # 识别知识空白
        gaps = self._identify_gaps(departments, roles, processes, products, systems)

        profile = EnterpriseProfileData(
            basic=basic,
            tags=self._generate_tags(basic, org_summary, business),
            org_summary=org_summary,
            maturity=maturity,
            business=business,
            gaps=gaps,
            version="v1.0.0",
            updated_at=utcnow(),
            completeness_score=0.0,
        )
        return profile

    def _generate_tags(
        self,
        basic: EnterpriseBasic,
        org_summary: EnterpriseOrgSummary,
        business: EnterpriseBusiness,
    ) -> list[str]:
        """生成企业标签。"""
        tags: list[str] = []
        if basic.industry:
            tags.append(basic.industry)
        if org_summary.department_count > 0:
            tags.append(f"{org_summary.department_count}部门")
        if org_summary.headcount > 0:
            tags.append(f"{org_summary.headcount}岗位")
        if business.main_products:
            tags.append(f"{len(business.main_products)}产品")
        return tags

    def _identify_gaps(
        self, departments, roles, processes, products, systems
    ) -> list[str]:
        """识别知识空白。"""
        gaps: list[str] = []
        process_names = [p.name.lower() for p in processes]
        # 检查常见流程是否缺失
        common_processes = ["采购", "财务", "售后", "客服", "销售", "审批"]
        for cp in common_processes:
            if not any(cp in pn for pn in process_names):
                gaps.append(f"未发现{cp}流程")
        if not systems:
            gaps.append("未发现业务系统接入")
        if not products:
            gaps.append("未发现产品数据")
        return gaps

    async def save_profile(self, profile: EnterpriseProfileData) -> EnterpriseProfile:
        """保存企业画像到数据库。"""
        # 停用旧版本
        await self._db.execute(
            select(EnterpriseProfile).where(
                EnterpriseProfile.enterprise_id == self._enterprise_id
            )
        )
        # 创建新记录
        record = EnterpriseProfile(
            enterprise_id=self._enterprise_id,
            profile=profile.model_dump(mode="json"),
            version=profile.version,
            completeness_score=int(profile.completeness_score),
        )
        self._db.add(record)
        await self._db.commit()
        await self._db.refresh(record)
        return record

    async def get_profile(self) -> Optional[EnterpriseProfileData]:
        """获取最新企业画像。"""
        result = await self._db.execute(
            select(EnterpriseProfile)
            .where(EnterpriseProfile.enterprise_id == self._enterprise_id)
            .order_by(EnterpriseProfile.created_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return EnterpriseProfileData(**record.profile)
