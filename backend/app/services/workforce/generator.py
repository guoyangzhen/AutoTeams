"""AI 员工生成器：6 步生成流程（PRD §5.11）。

6 步流程：
1. 岗位提取：从 CapabilityMatrix（WT1）提取所有已建模岗位
2. 优先级排序：按主流程参与度排序
3. Agent 配置生成：匹配 AgentConfigTemplate（WT2）+ 预置模板
4. 推荐展示：返回推荐列表
5. 用户确认：接受 confirmed_position_ids
6. 正式创建：创建 Agent，注入配置，状态设为"试用"（Recruit）

依赖：
- WT1 CapabilityMatrix（spec.md §10.3）—— 从 CompilationArtifact 读取
- WT2 AgentConfigTemplate（spec.md §10.5）—— 从 runtime_query 读取
- template_service.apply_agent_template —— 底层 Agent 创建
"""
import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.agent import Agent
from app.models.compiler import CompilationArtifact
from app.schemas.compiler import (
    CapabilityMatrix,
    PositionCapability,
    SkillRequirement,
    KnowledgeRequirement,
    ToolRequirement,
)
from app.services.workforce.position_matcher import PositionMatcher
from app.services.compiler.capability_compiler import is_noise_role_name
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 企业 AI 员工总数上限：AI 员工只需保持 10 位（5 P0 + 5 P1 核心岗位）
MAX_AGENTS_PER_ENTERPRISE = 10


# ============================================================================
# 默认 MVP 能力矩阵（WT1 未编译时使用）
# ============================================================================

def _default_mvp_capability_matrix(enterprise_id: str) -> CapabilityMatrix:
    """构建 MVP 默认能力矩阵（10 个核心岗位）。

    覆盖企业核心业务岗位：销售、售前、财务、客服、售后、采购、人事、市场、物流、技术。
    P0: 销售代表 / 售前技术支持 / 财务经理 / 客服专员 / 售后服务专员
    P1: 采购专员 / 人事专员 / 市场专员 / 物流专员 / 技术工程师
    """
    positions = [
        PositionCapability(
            position_id="pos_sales",
            position_name="销售代表",
            department="销售部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="线索评分", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="销售话术生成", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="跟进计划生成", skill_type="hard", proficiency_level="basic", source="sop"),
                SkillRequirement(skill_name="CRM字段提取", skill_type="hard", proficiency_level="basic", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="产品介绍", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="竞品分析", coverage=0.5),
                KnowledgeRequirement(knowledge_domain="客户案例", coverage=0.6),
            ],
            required_tools=[
                ToolRequirement(tool_name="CRM系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["sales.view", "sales.quote", "customer.view"],
            kpi_ids=["kpi_sales_revenue", "kpi_conversion_rate"],
            main_processes=["proc_sales_lead", "proc_sales_quote", "proc_sales_followup"],
            priority="P0",
        ),
        PositionCapability(
            position_id="pos_pre_sales",
            position_name="售前技术支持",
            department="销售部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="产品参数查询", skill_type="hard", proficiency_level="advanced", source="sop"),
                SkillRequirement(skill_name="技术方案生成", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="产品规格", coverage=0.9),
                KnowledgeRequirement(knowledge_domain="技术文档", coverage=0.7),
            ],
            required_tools=[
                ToolRequirement(tool_name="知识库检索", tool_type="api", required_permissions=["read"]),
            ],
            required_permissions=["product.view", "knowledge.search"],
            kpi_ids=["kpi_presales_response_time", "kpi_presales_satisfaction"],
            main_processes=["proc_presales_consult", "proc_presales_proposal"],
            priority="P0",
        ),
        PositionCapability(
            position_id="pos_finance",
            position_name="财务经理",
            department="财务部",
            level="L3",
            required_skills=[
                SkillRequirement(skill_name="财报摘要生成", skill_type="hard", proficiency_level="advanced", source="sop"),
                SkillRequirement(skill_name="合规检查", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="信用风险评估", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="财务报表", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="合规制度", coverage=0.9),
            ],
            required_tools=[
                ToolRequirement(tool_name="财务系统", tool_type="api", required_permissions=["read", "approve"]),
            ],
            required_permissions=["finance.view", "finance.approve", "finance.audit"],
            kpi_ids=["kpi_finance_accuracy", "kpi_finance_compliance"],
            main_processes=["proc_finance_review", "proc_finance_approval"],
            priority="P0",
        ),
        PositionCapability(
            position_id="pos_customer_service",
            position_name="客服专员",
            department="客服部",
            level="L1",
            required_skills=[
                SkillRequirement(skill_name="工单分类", skill_type="hard", proficiency_level="basic", source="sop"),
                SkillRequirement(skill_name="FAQ知识问答", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="情感分析", skill_type="soft", proficiency_level="basic", source="sop"),
                SkillRequirement(skill_name="工单升级判定", skill_type="hard", proficiency_level="basic", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="FAQ库", coverage=0.9),
                KnowledgeRequirement(knowledge_domain="服务规范", coverage=0.8),
            ],
            required_tools=[
                ToolRequirement(tool_name="工单系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["ticket.view", "ticket.handle", "customer.view"],
            kpi_ids=["kpi_cs_satisfaction", "kpi_cs_response_time"],
            main_processes=["proc_cs_ticket", "proc_cs_faq"],
            priority="P1",
        ),
        PositionCapability(
            position_id="pos_after_sales",
            position_name="售后服务专员",
            department="客服部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="售后问题分类", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="售后方案生成", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="售后流程", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="产品维护", coverage=0.6),
            ],
            required_tools=[
                ToolRequirement(tool_name="工单系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["ticket.view", "ticket.handle", "customer.view"],
            kpi_ids=["kpi_after_sales_resolution", "kpi_after_sales_satisfaction"],
            main_processes=["proc_after_sales_receive", "proc_after_sales_resolve"],
            priority="P1",
        ),
        PositionCapability(
            position_id="pos_procurement",
            position_name="采购专员",
            department="采购部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="供应商评估", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="采购订单生成", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="供应商档案", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="采购流程", coverage=0.9),
            ],
            required_tools=[
                ToolRequirement(tool_name="采购系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["procurement.view", "procurement.create", "supplier.view"],
            kpi_ids=["kpi_procurement_cost", "kpi_procurement_ontime"],
            main_processes=["proc_procurement_request", "proc_procurement_order"],
            priority="P1",
        ),
        PositionCapability(
            position_id="pos_hr",
            position_name="人事专员",
            department="人事部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="简历筛选", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="面试安排", skill_type="hard", proficiency_level="basic", source="sop"),
                SkillRequirement(skill_name="员工档案管理", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="招聘流程", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="劳动法规", coverage=0.7),
            ],
            required_tools=[
                ToolRequirement(tool_name="人事系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["hr.view", "hr.manage", "employee.view"],
            kpi_ids=["kpi_hr_recruitment", "kpi_hr_retention"],
            main_processes=["proc_hr_recruit", "proc_hr_onboard"],
            priority="P1",
        ),
        PositionCapability(
            position_id="pos_marketing",
            position_name="市场专员",
            department="市场部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="营销文案生成", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="活动策划", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="数据分析", skill_type="hard", proficiency_level="basic", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="品牌资料", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="市场行情", coverage=0.6),
            ],
            required_tools=[
                ToolRequirement(tool_name="营销平台", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["marketing.view", "marketing.publish", "analytics.view"],
            kpi_ids=["kpi_marketing_reach", "kpi_marketing_conversion"],
            main_processes=["proc_marketing_plan", "proc_marketing_execute"],
            priority="P1",
        ),
        PositionCapability(
            position_id="pos_logistics",
            position_name="物流专员",
            department="物流部",
            level="L2",
            required_skills=[
                SkillRequirement(skill_name="发货安排", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="物流跟踪", skill_type="hard", proficiency_level="basic", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="物流流程", coverage=0.8),
                KnowledgeRequirement(knowledge_domain="仓储信息", coverage=0.7),
            ],
            required_tools=[
                ToolRequirement(tool_name="物流系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["logistics.view", "logistics.manage", "inventory.view"],
            kpi_ids=["kpi_logistics_ontime", "kpi_logistics_accuracy"],
            main_processes=["proc_logistics_ship", "proc_logistics_track"],
            priority="P1",
        ),
        PositionCapability(
            position_id="pos_tech",
            position_name="技术工程师",
            department="技术部",
            level="L3",
            required_skills=[
                SkillRequirement(skill_name="技术文档检索", skill_type="hard", proficiency_level="advanced", source="sop"),
                SkillRequirement(skill_name="故障诊断", skill_type="hard", proficiency_level="intermediate", source="sop"),
                SkillRequirement(skill_name="代码生成", skill_type="hard", proficiency_level="intermediate", source="sop"),
            ],
            required_knowledge=[
                KnowledgeRequirement(knowledge_domain="技术文档", coverage=0.9),
                KnowledgeRequirement(knowledge_domain="系统架构", coverage=0.7),
            ],
            required_tools=[
                ToolRequirement(tool_name="知识库检索", tool_type="api", required_permissions=["read"]),
                ToolRequirement(tool_name="工单系统", tool_type="api", required_permissions=["read", "write"]),
            ],
            required_permissions=["tech.view", "tech.manage", "knowledge.search"],
            kpi_ids=["kpi_tech_resolution", "kpi_tech_satisfaction"],
            main_processes=["proc_tech_diagnose", "proc_tech_resolve"],
            priority="P1",
        ),
    ]

    return CapabilityMatrix(
        enterprise_id=enterprise_id,
        positions=positions,
        compiled_at=utcnow(),
        confidence=0.7,
    )


# 岗位 ID → 预置模板 role 映射
_POSITION_TO_TEMPLATE_ROLE = {
    "pos_sales": "sales",
    "pos_pre_sales": "pre_sales",
    "pos_finance": "finance",
    "pos_customer_service": "customer_service",
    "pos_after_sales": "after_sales",
    "pos_procurement": "procurement",
    "pos_hr": "hr",
    "pos_marketing": "marketing",
    "pos_logistics": "logistics",
    "pos_tech": "tech",
}

# 非活跃/离职类岗位关键词：企业组织数据中提取到这些节点时，
# 不应作为 AI 数字员工推荐（它们不是可协作的岗位）。
_OFFBOARD_KEYWORDS = (
    "离职", "退休", "离岗", "辞退", "解聘",
    "terminated", "retired", "inactive", "offboard", "exited",
)

# 非岗位关系标签：组织架构中表示关系/特殊节点的名称，不是可招聘的岗位。
_NON_POSITION_LABELS = (
    "直属上级", "接收人", "法定代表人", "部门经理本人", "本人",
)


def _is_offboarded_position(position_name: str) -> bool:
    """判断岗位名是否属于离职/退休等非活跃节点或关系标签。"""
    if not position_name:
        return False
    name_lower = position_name.lower()
    if any(kw in position_name or kw in name_lower for kw in _OFFBOARD_KEYWORDS):
        return True
    return any(label in position_name for label in _NON_POSITION_LABELS)


# 岗位名称关键词 → 预置模板 role 映射（用于动态匹配）
_NAME_TO_ROLE_KEYWORDS = {
    "销售": "sales",
    "售前": "pre_sales",
    "财务": "finance",
    "客服": "customer_service",
    "售后": "after_sales",
    "采购": "procurement",
    "人事": "hr",
    "市场": "marketing",
    "物流": "logistics",
    "技术": "tech",
}


class WorkforceGenerator:
    """AI 员工 6 步生成器。

    用法：
        gen = WorkforceGenerator()
        # 步骤 1-4: 生成推荐
        result = await gen.generate(db, enterprise_id)
        # 步骤 5-6: 确认并创建
        result = await gen.confirm(db, enterprise_id, confirmed_position_ids)
    """

    def __init__(self, matcher: Optional[PositionMatcher] = None):
        self.matcher = matcher or PositionMatcher()

    # ========================================================================
    # 步骤 1-4: generate（岗位提取 → 排序 → 配置生成 → 推荐展示）
    # ========================================================================

    async def generate(
        self,
        db: AsyncSession,
        enterprise_id: str,
        max_positions: int = 10,
    ) -> dict:
        """执行 6 步生成流程的前 4 步（返回推荐列表）。

        Args:
            max_positions: 最多返回的推荐岗位数量（按优先级截取前 N 个核心岗位，
                默认 10，覆盖销售/售前/财务/客服/售后/采购/人事/市场/物流/技术）。

        Returns:
            {recommendations: [...], total: int}
        """
        # 步骤 1: 岗位提取 —— 从 WT1 CapabilityMatrix 读取
        capability_matrix = await self._load_capability_matrix(db, enterprise_id)

        # 步骤 2: 优先级排序
        positions = self._sort_by_priority(capability_matrix.positions)

        # 步骤 2.3: 过滤非岗位型节点（离职/退休/法人等非实际 AI 岗位）。
        # 企业组织数据中可能包含「离职员工」「退休」等非活跃节点，
        # 不应作为 AI 数字员工推荐，否则会生成无意义的「离职员工」推荐。
        positions = [
            p for p in positions
            if not _is_offboarded_position(p.position_name)
        ]

        # 步骤 2.4: 过滤文件/目录名等噪音岗位（能力矩阵可能被污染时兜底）。
        # NER/文件名回退会把 README、01-company、sales-sop-v1 等当作物岗位，
        # 二次过滤保证推荐列表里不会出现「文件名」AI 员工。
        positions = [
            p for p in positions
            if not is_noise_role_name(p.position_name)
        ]

        # 步骤 2.5: 截取前 max_positions 个核心岗位（避免返回过多低优先级岗位）
        if max_positions > 0 and len(positions) > max_positions:
            positions = positions[:max_positions]
            logger.info(
                f"Workforce 截取核心岗位: enterprise={enterprise_id}, "
                f"截取前 {max_positions} 个（共 {len(capability_matrix.positions)} 个）"
            )

        # 步骤 3: Agent 配置生成 —— 匹配 WT2 AgentConfigTemplate
        agent_templates = await self._load_agent_templates(db, enterprise_id)
        matches = self.matcher.match(
            CapabilityMatrix(
                enterprise_id=enterprise_id,
                positions=positions,
                compiled_at=capability_matrix.compiled_at,
                confidence=capability_matrix.confidence,
            ),
            agent_templates,
        )

        # 步骤 4: 推荐展示
        recommendations = [m.to_dict() for m in matches]

        logger.info(
            f"Workforce 生成完成: enterprise={enterprise_id}, "
            f"推荐 {len(recommendations)} 个岗位"
        )

        return {
            "recommendations": recommendations,
            "total": len(recommendations),
        }

    # ========================================================================
    # 步骤 5-6: confirm（用户确认 → 正式创建）
    # ========================================================================

    async def confirm(
        self,
        db: AsyncSession,
        enterprise_id: str,
        confirmed_position_ids: list[str],
        adjustments: Optional[dict] = None,
    ) -> dict:
        """执行 6 步生成流程的后 2 步（确认 + 创建）。

        安全修复：
        - position_id 去重：跳过已有 Agent 的岗位，防止重复创建
        - 总数限制：企业 Agent 总数不超过 MAX_AGENTS_PER_ENTERPRISE（默认 10）

        Returns:
            {created_agents: [...], failed: [...], skipped: [...]}
        """
        # 重新加载能力矩阵以获取岗位详情
        capability_matrix = await self._load_capability_matrix(db, enterprise_id)
        position_map = {p.position_id: p for p in capability_matrix.positions}

        # 查询企业已有的 Agent position_id 集合，用于去重
        existing_stmt = (
            select(Agent.position_id)
            .where(
                Agent.enterprise_id == enterprise_id,
                Agent.position_id.isnot(None),
            )
        )
        existing_result = await db.execute(existing_stmt)
        existing_position_ids = {row[0] for row in existing_result.fetchall()}

        # 查询企业当前 Agent 总数，用于限制
        count_stmt = select(func.count(Agent.id)).where(Agent.enterprise_id == enterprise_id)
        count_result = await db.execute(count_stmt)
        current_agent_count = count_result.scalar() or 0

        created_agents: list[dict] = []
        failed: list[dict] = []
        skipped: list[dict] = []

        for position_id in confirmed_position_ids:
            # 去重：跳过已有 Agent 的岗位
            if position_id in existing_position_ids:
                position = position_map.get(position_id)
                skipped.append({
                    "position_id": position_id,
                    "position_name": position.position_name if position else "未知",
                    "reason": "该岗位已有 AI 员工，跳过重复创建",
                })
                logger.info(f"跳过重复岗位: position_id={position_id}, enterprise={enterprise_id}")
                continue

            # 总数限制：不超过 MAX_AGENTS_PER_ENTERPRISE
            if current_agent_count >= MAX_AGENTS_PER_ENTERPRISE:
                skipped.append({
                    "position_id": position_id,
                    "position_name": position_map.get(position_id, None).position_name if position_id in position_map else "未知",
                    "reason": f"企业 AI 员工总数已达上限 ({MAX_AGENTS_PER_ENTERPRISE})",
                })
                logger.warning(
                    f"Agent 总数限制: enterprise={enterprise_id}, "
                    f"已达上限 {MAX_AGENTS_PER_ENTERPRISE}"
                )
                break

            position = position_map.get(position_id)
            if not position:
                failed.append({
                    "position_id": position_id,
                    "position_name": "未知",
                    "error": f"岗位 {position_id} 不存在于能力矩阵中",
                })
                continue

            try:
                agent = await self._create_agent(db, enterprise_id, position)
                created_agents.append({
                    "agent_id": agent.id,
                    "position_id": position.position_id,
                    "position_name": position.position_name,
                    "lifecycle_stage": agent.lifecycle_stage or "recruit",
                })
                current_agent_count += 1
                existing_position_ids.add(position_id)
            except Exception as e:
                logger.error(f"创建 Agent 失败: position={position.position_name}, error={e}")
                failed.append({
                    "position_id": position.position_id,
                    "position_name": position.position_name,
                    "error": str(e),
                })

        logger.info(
            f"Workforce 确认完成: enterprise={enterprise_id}, "
            f"成功 {len(created_agents)} / 失败 {len(failed)} / 跳过 {len(skipped)}"
        )

        return {
            "created_agents": created_agents,
            "failed": failed,
            "skipped": skipped,
        }

    # ========================================================================
    # 内部方法
    # ========================================================================

    async def _load_capability_matrix(
        self,
        db: AsyncSession,
        enterprise_id: str,
    ) -> CapabilityMatrix:
        """从 WT1 CompilationArtifact 读取 CapabilityMatrix。

        如果 WT1 尚未编译，返回默认 MVP 能力矩阵。
        """
        # 查询最新的 capability 级编译产物
        stmt = (
            select(CompilationArtifact)
            .where(
                CompilationArtifact.enterprise_id == enterprise_id,
                CompilationArtifact.stage == "capability",
            )
            .order_by(CompilationArtifact.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        artifact = result.scalar_one_or_none()

        if artifact and artifact.output:
            try:
                # output 结构: CapabilityCompileOutput 的 dict 表示
                output_data = artifact.output
                if isinstance(output_data, dict):
                    cm_data = output_data.get("capability_matrix", output_data)
                    return CapabilityMatrix.model_validate(cm_data)
            except Exception as e:
                logger.warning(f"解析 CapabilityMatrix 失败，使用默认值: {e}")

        # WT1 未编译，使用默认 MVP 能力矩阵
        logger.info(f"WT1 未编译，使用默认 MVP 能力矩阵: enterprise={enterprise_id}")
        return _default_mvp_capability_matrix(enterprise_id)

    async def _load_agent_templates(self, db: AsyncSession, enterprise_id: str) -> list:
        """从 WT2 runtime_query 读取 AgentConfigTemplate 列表。"""
        try:
            from app.services.runtime.runtime_query import get_agent_templates
            templates = await get_agent_templates(db, enterprise_id)
            return templates
        except Exception as e:
            logger.warning(f"读取 WT2 Agent 模板失败（使用空列表）: {e}")
            return []

    def _sort_by_priority(self, positions: list[PositionCapability]) -> list[PositionCapability]:
        """步骤 2: 按优先级排序（P0 > P1 > P2，同优先级按主流程参与度）。"""
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        return sorted(
            positions,
            key=lambda p: (
                priority_order.get(p.priority, 3),
                -len(p.main_processes),
            ),
        )

    async def _create_agent(
        self,
        db: AsyncSession,
        enterprise_id: str,
        position: PositionCapability,
    ) -> Agent:
        """步骤 6: 正式创建 Agent。

        策略：
        1. 查找匹配的预置模板（template_service）
        2. 尝试使用 apply_agent_template 创建（需要 folder_path）
        3. 如果失败，直接创建 Agent 记录（轻量模式）
        4. 设置 workforce 字段（lifecycle_stage/position_id/memory_config/kpi_ids）
        """
        # 尝试使用 template_service.apply_agent_template
        try:
            agent = await self._create_via_template(db, enterprise_id, position)
            if agent:
                return agent
        except Exception as e:
            logger.warning(
                f"通过模板创建 Agent 失败，降级为直接创建: {e}",
                exc_info=True,
            )

        # 降级：直接创建 Agent 记录
        return await self._create_directly(db, enterprise_id, position)

    async def _create_via_template(
        self,
        db: AsyncSession,
        enterprise_id: str,
        position: PositionCapability,
    ) -> Optional[Agent]:
        """通过 template_service.apply_agent_template 创建 Agent。"""
        from app.services.template_service import (
            list_templates,
            apply_agent_template,
            ensure_preset_templates,
        )

        # 确保预置模板已初始化
        await ensure_preset_templates(db)

        # 查找匹配的预置模板
        target_role = self._get_template_role(position)
        if not target_role:
            return None

        templates = await list_templates(db, enterprise_id=enterprise_id)
        matching_template = None
        for tpl in templates:
            if tpl.role == target_role:
                matching_template = tpl
                break

        if not matching_template:
            logger.warning(f"未找到匹配的预置模板: role={target_role}")
            return None

        # 查找 folder_path（从现有 Agent 获取）
        folder_path = await self._find_folder_path(db, enterprise_id)
        if not folder_path:
            return None  # 无 folder_path 则降级

        # 调用 apply_agent_template
        result = await apply_agent_template(
            db=db,
            db_session_factory=async_session_factory,
            template_id=matching_template.id,
            enterprise_id=enterprise_id,
            folder_path=folder_path,
            name_override=f"{position.position_name}（AI）",
            description_override=f"基于岗位能力矩阵自动生成 - {position.position_name}",
        )

        agent_id = result.get("agent_id")
        if not agent_id:
            return None

        # 更新 workforce 字段
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent:
            agent.lifecycle_stage = "recruit"
            agent.position_id = position.position_id
            agent.memory_config = {
                "short_term": {"max_turns": 20},
                "long_term": {"enabled": True},
                "entity_memory": {"enabled": True},
            }
            agent.kpi_ids = position.kpi_ids
            await db.commit()
            await db.refresh(agent)

        return agent

    async def _create_directly(
        self,
        db: AsyncSession,
        enterprise_id: str,
        position: PositionCapability,
    ) -> Agent:
        """直接创建 Agent 记录（轻量模式，不扫描文件）。"""
        agent = Agent(
            enterprise_id=enterprise_id,
            name=f"{position.position_name}（AI）",
            description=f"基于岗位能力矩阵自动生成 - {position.position_name}",
            system_prompt=self._generate_system_prompt(position),
            status="ready",
            version="1.0.0",
            config={},
            lifecycle_stage="recruit",
            position_id=position.position_id,
            memory_config={
                "short_term": {"max_turns": 20},
                "long_term": {"enabled": True},
                "entity_memory": {"enabled": True},
            },
            kpi_ids=position.kpi_ids,
        )
        db.add(agent)
        await db.flush()
        await db.commit()
        await db.refresh(agent)

        # 记录生命周期
        from app.models.workforce import WorkforceLifecycle
        lifecycle = WorkforceLifecycle(
            agent_id=agent.id,
            enterprise_id=enterprise_id,
            stage="recruit",
            stage_entered_at=utcnow(),
            transition_reason="AI 员工生成（直接创建）",
        )
        db.add(lifecycle)
        await db.commit()

        logger.info(f"Agent 直接创建完成: id={agent.id}, position={position.position_name}")
        return agent

    async def _find_folder_path(self, db: AsyncSession, enterprise_id: str) -> Optional[str]:
        """从现有 Agent 查找可用的 folder_path。"""
        stmt = (
            select(Agent.folder_path)
            .where(
                Agent.enterprise_id == enterprise_id,
                Agent.folder_path.isnot(None),
            )
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    def _get_template_role(self, position: PositionCapability) -> Optional[str]:
        """根据岗位获取匹配的预置模板 role。"""
        # 1. 精确匹配 position_id
        if position.position_id in _POSITION_TO_TEMPLATE_ROLE:
            return _POSITION_TO_TEMPLATE_ROLE[position.position_id]

        # 2. 关键词匹配 position_name
        for keyword, role in _NAME_TO_ROLE_KEYWORDS.items():
            if keyword in position.position_name:
                return role

        return None

    def _generate_system_prompt(self, position: PositionCapability) -> str:
        """根据岗位能力生成默认 system_prompt。"""
        skills = "、".join(s.skill_name for s in position.required_skills[:5])
        knowledge = "、".join(k.knowledge_domain for k in position.required_knowledge[:3])
        return (
            f"你是「{position.position_name}」，隶属于企业的 AI 数字员工。\n\n"
            f"岗位职责：负责 {position.department} 的 {position.position_name} 工作。\n"
            f"核心技能：{skills or '待配置'}\n"
            f"知识领域：{knowledge or '待配置'}\n"
            f"参与流程：{', '.join(position.main_processes) or '待配置'}\n\n"
            f"工作规范：\n"
            f"- 基于知识库信息回答，缺失时明确告知\n"
            f"- 保持专业、礼貌、简洁\n"
            f"- 涉及敏感操作时引导至人工处理\n"
        )
