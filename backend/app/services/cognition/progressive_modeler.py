"""渐进式企业建模器。

触发机制：文件上传/系统连接/访谈回答/SOP 修改/定时触发。所有重编译均提交到
CompilationJob 耐久队列，由独立 Worker 领取；不再生成无输入快照且无法执行的 pending
记录。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.compiler import CompilationJob
from app.services.compiler.job_queue import enqueue_compilation_job

logger = logging.getLogger(__name__)


class ProgressiveModeler:
    """根据变更源提交可恢复的增量重编译任务。"""

    TRIGGER_STAGE_MAP = {
        "file_upload": ["information", "knowledge"],
        "file_change": ["information", "knowledge"],
        "system_connect": ["information", "knowledge"],
        "interview_answer": ["process", "capability", "runtime"],
        "sop_change": ["process", "capability", "runtime"],
        "kpi_change": ["capability", "runtime"],
        "organization_change": ["knowledge", "process", "capability", "runtime"],
        "scheduled": ["information", "knowledge", "process", "capability", "runtime"],
    }

    def __init__(self, db: AsyncSession, enterprise_id: str):
        self._db = db
        self._enterprise_id = enterprise_id

    def analyze_affected_stages(self, trigger_source: str) -> list[str]:
        stages = self.TRIGGER_STAGE_MAP.get(trigger_source, ["information"])
        logger.info("渐进式建模：触发源=%s, 受影响层级=%s", trigger_source, stages)
        return stages

    async def _latest_completed_folder_path(self) -> str | None:
        """取得该企业最近一次成功编译的持久化输入目录。

        只复用 completed Job 的快照，避免把失败、取消或正在运行任务的临时输入传播到
        新作业。没有快照时由调用方显式提供目录，而不是留下永久 pending Job。
        """
        result = await self._db.execute(
            select(CompilationJob.folder_path)
            .where(
                CompilationJob.enterprise_id == self._enterprise_id,
                CompilationJob.status == "completed",
                CompilationJob.folder_path.is_not(None),
            )
            .order_by(CompilationJob.completed_at.desc(), CompilationJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def trigger_recompile(
        self,
        trigger_source: str,
        affected_stages: Optional[list[str]] = None,
        folder_path: str | None = None,
    ) -> CompilationJob:
        """提交增量重编译到耐久队列。

        ``folder_path`` 可由已校验的 API 请求提供；未提供时只复用本企业最近一次成功
        编译的输入快照。若两者均不存在，显式失败以提示调用方提供可恢复的输入。
        """
        stages = affected_stages or self.analyze_affected_stages(trigger_source)
        resolved_path = folder_path or await self._latest_completed_folder_path() or ""

        # 没有可恢复的输入就显式失败，不要把空路径丢进队列：worker 会领走它、
        # 扫不到任何文件，最后以"成功但零产物"收场 —— 那比直接报错更难排查。
        if not resolved_path.strip():
            raise ValueError("recompile_requires_folder_path_or_completed_compilation")

        job, created = await enqueue_compilation_job(
            self._db,
            enterprise_id=self._enterprise_id,
            folder_path=resolved_path,
            trigger_source=trigger_source,
        )

        # affected_stages 是渐进式建模的审计信息。对于去重返回的活动 Job，合并而不是
        # 覆盖，确保并发事件不会丢失受影响层级。
        existing_stages = [item for item in (job.affected_stages or "").split(",") if item]
        merged_stages = list(dict.fromkeys([*existing_stages, *stages]))
        if merged_stages and job.stage != merged_stages[0]:
            job.stage = merged_stages[0]
        if job.affected_stages != ",".join(merged_stages):
            job.affected_stages = ",".join(merged_stages)
            await self._db.commit()
            await self._db.refresh(job)

        logger.info(
            "渐进式建模任务已%s: job_id=%s enterprise_id=%s trigger=%s",
            "创建" if created else "去重复用",
            job.id,
            self._enterprise_id,
            trigger_source,
        )
        return job

    def generate_user_feedback(self, job: CompilationJob) -> str:
        """生成用户可见的变更反馈。"""
        feedback_map = {
            "file_upload": "新发现文件已触发信息与知识层重编译",
            "file_change": "文件变更已触发信息与知识层重编译",
            "system_connect": "已连接外部系统，正在同步数据到知识图谱",
            "interview_answer": "访谈回答已更新运行模型规则",
            "sop_change": "SOP 修改已触发流程与能力层重编译",
            "kpi_change": "KPI 变更已触发能力层重编译",
            "organization_change": "组织架构变更已触发全量重编译",
            "scheduled": "定时触发全量重编译",
        }
        base = feedback_map.get(job.trigger_source, "已触发重编译")
        if job.affected_stages:
            stage_names = {
                "information": "信息", "knowledge": "知识", "process": "流程",
                "capability": "能力", "runtime": "运行时",
            }
            names = [stage_names.get(stage, stage) for stage in job.affected_stages.split(",")]
            base += f"，受影响层级：{'→'.join(names)}"
        return base


async def trigger_recompile_background(
    enterprise_id: str,
    trigger_source: str,
    affected_stages: Optional[list[str]] = None,
    folder_path: str | None = None,
) -> str:
    """供文件监听器等后台触发器提交耐久重编译任务。"""
    async with async_session_factory() as db:
        modeler = ProgressiveModeler(db, enterprise_id)
        job = await modeler.trigger_recompile(trigger_source, affected_stages, folder_path)
        return str(job.id)
