"""编译器编排器（PRD §5.2 编译交互流程）。

串联五级编译器 + 完成度计算 + 业务嵌入，形成端到端编译 Pipeline。
支持全量编译和增量重编译（渐进式建模）。
"""
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import async_session_factory
from app.models.compiler import CompilationJob, CompilationArtifact
from app.services.compiler.base import CompilationContext, CompilationResult, Stage
from app.services.compiler.information_compiler import InformationCompiler
from app.services.compiler.knowledge_compiler import KnowledgeCompiler
from app.services.compiler.process_compiler import ProcessCompiler
from app.services.compiler.capability_compiler import CapabilityCompiler
from app.services.compiler.runtime_compiler import RuntimeCompiler
from app.services.compiler.completeness import CompletenessCalculator
from app.services.embedding.business_embedder import BusinessEmbedder
from app.services.runtime import save_runtime
from app.schemas.compiler import (
    RuntimeCompileResult,
    CapabilityMatrix,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class CompilationPipeline:
    """编译流水线编排器。

    PRD §5.2 编译交互流程：
    用户上传文件夹 → Information → Knowledge → Process → Capability → Runtime
    → 完成度计算 → 业务嵌入 → 持久化（WT2 负责）

    支持增量重编译：只执行受影响层级。
    """

    # 编译器执行顺序
    STAGE_ORDER = [
        Stage.INFORMATION,
        Stage.KNOWLEDGE,
        Stage.PROCESS,
        Stage.CAPABILITY,
        Stage.RUNTIME,
    ]

    STAGE_COMPILERS = {
        Stage.INFORMATION: InformationCompiler,
        Stage.KNOWLEDGE: KnowledgeCompiler,
        Stage.PROCESS: ProcessCompiler,
        Stage.CAPABILITY: CapabilityCompiler,
        Stage.RUNTIME: RuntimeCompiler,
    }

    def __init__(
        self,
        db: AsyncSession,
        enterprise_id: str,
        *,
        expected_worker_id: str | None = None,
    ):
        self._db = db
        self._enterprise_id = enterprise_id
        # 队列 Worker 必须在持久化边界确认租约仍归自己；直接 API/单元测试路径
        # 可保留 None，以维持既有同步调用契约。
        self._expected_worker_id = expected_worker_id

    async def run_full(
        self,
        folder_path: str,
        job_id: Optional[str] = None,
        interview_completion: float = 0.0,
    ) -> dict[str, Any]:
        """执行全量五级编译。

        Args:
            folder_path: 企业文件文件夹路径
            job_id: 编译任务 ID（可选，由 API 层创建）
            interview_completion: 访谈完成度

        Returns:
            包含 runtime / capability_matrix / gaps / completeness 的字典
        """
        # 创建或加载编译任务
        job = await self._get_or_create_job(job_id, "manual")

        ctx = CompilationContext(
            enterprise_id=self._enterprise_id,
            db=self._db,
            folder_path=folder_path,
            upstream={},
        )

        results: dict[str, CompilationResult] = {}

        # 按顺序执行五级编译。每个阶段边界检查取消标记和 Worker 租约，
        # 避免已撤销或被接管的旧执行者继续写入任务状态与产物。
        total_stages = len(self.STAGE_ORDER)
        for idx, stage in enumerate(self.STAGE_ORDER):
            await self._assert_job_execution_active(job)

            compiler_cls = self.STAGE_COMPILERS[stage]
            compiler = compiler_cls()
            # 进入该级：先写「阶段开始」进度，让前端管道动画立刻推进到本级
            job.stage = stage.value
            job.stage_started_at = utcnow()
            job.progress = round(idx / total_stages, 4)
            await self._db.commit()

            stage_begin = utcnow()
            result = await compiler.compile(ctx)
            duration_ms = int((utcnow() - stage_begin).total_seconds() * 1000)

            results[stage.value] = result
            # 将输出注入 upstream 供下一级使用
            ctx.upstream[stage.value] = result.output
            # 保存 artifact（含实际耗时，供回放模式按真实时序播放）
            if job.id:
                await compiler.save_artifact(ctx, result, job.id, duration_ms=duration_ms)
            # 该级完成：进度推进到 (idx+1)/total
            job.confidence = result.confidence
            job.progress = round((idx + 1) / total_stages, 4)
            # 每级编译后立即 commit，释放 SQLite 写锁，
            # 避免长时间 LLM 调用期间阻塞 API 并发查询（"database is locked"）
            await self._db.commit()

        # 最后一级可能是耗时 LLM 调用；在计算与 Runtime 持久化前复核取消/租约，
        # 防止已失去执行权的 Worker 写出新版本。
        await self._assert_job_execution_active(job)

        # 完成度计算
        calculator = CompletenessCalculator(self._enterprise_id)
        completeness = calculator.calculate(ctx, interview_completion)

        # 业务嵌入
        runtime_output = ctx.upstream.get(Stage.RUNTIME.value)
        if runtime_output is not None:
            runtime_result = self._get_runtime_result(runtime_output)
            embedder = BusinessEmbedder(self._enterprise_id)
            runtime_result = embedder.embed(runtime_result)
            # 回填完成度
            runtime_result = runtime_result.model_copy(update={
                "completeness": completeness.overall
            })
            ctx.upstream[Stage.RUNTIME.value] = runtime_result

        # 持久化 Enterprise Runtime 到 enterprise_runtimes 表
        # （供 /api/v1/runtime/* 端点和前端 BossDashboard/RuntimeView 消费）
        avg_conf = self._avg_confidence(results)
        runtime_to_persist = self._get_runtime_result(ctx.upstream.get(Stage.RUNTIME.value))
        if runtime_to_persist is not None:
            await self._persist_runtime(runtime_to_persist, completeness.overall, avg_conf)

        # 更新任务完成状态
        # P2 字段对齐: stage 保留为最后完成的阶段名（runtime），
        # 不使用 "complete"（不在前端 CompilerStageName 字面量范围内）
        job.status = "completed"
        job.stage = Stage.RUNTIME.value
        job.confidence = avg_conf
        job.completeness = completeness.overall
        job.progress = 1.0
        job.completed_at = utcnow()
        await self._assert_job_execution_active(job)
        await self._db.commit()

        # 构建 WT 接口契约产出
        capability_matrix = self._get_capability_matrix(ctx.upstream.get(Stage.CAPABILITY.value))
        gaps = calculator.to_gaps_contract(completeness)
        runtime = self._get_runtime_result(ctx.upstream.get(Stage.RUNTIME.value))

        return {
            "job_id": job.id,
            "runtime": runtime,
            "capability_matrix": capability_matrix,
            "gaps": gaps,
            "completeness": completeness,
            "results": {k: v.discovered_summary for k, v in results.items()},
        }

    async def run_incremental(
        self,
        job_id: str,
        affected_stages: list[str],
        interview_completion: float = 0.0,
        *,
        folder_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """执行增量重编译（渐进式建模）。

        只重新执行受影响层级，复用未受影响层级的 artifact。

        Args:
            job_id: 编译任务 ID
            affected_stages: 受影响的编译层级
            interview_completion: 访谈完成度

        Returns:
            编译结果字典
        """
        job = await self._get_job(job_id)
        if job is None:
            raise ValueError(f"编译任务不存在: {job_id}")

        # 加载已有 artifact 作为 upstream
        ctx = CompilationContext(
            enterprise_id=self._enterprise_id,
            db=self._db,
            folder_path=folder_path,
            upstream={},
        )
        # 失败后重试时当前 Job 的已保存产物优先；未受影响的上游阶段从本企业
        # 最近一次完成编译补齐，避免后续增量任务因上下文不完整而退化。
        await self._load_artifacts(ctx, job.id)
        await self._load_latest_completed_artifacts(ctx, exclude_job_id=job.id)

        # 确定需要执行的层级（受影响层级 + 其下游层级）
        stage_enums = [Stage(s) for s in affected_stages if s in [s.value for s in Stage]]
        stages_to_run = self._compute_downstream_stages(stage_enums)

        job.status = "running"
        job.started_at = utcnow()
        await self._db.flush()

        results: dict[str, CompilationResult] = {}
        total_to_run = max(1, len(stages_to_run))
        ran = 0
        for stage in self.STAGE_ORDER:
            if stage in stages_to_run:
                await self._assert_job_execution_active(job)
                compiler_cls = self.STAGE_COMPILERS[stage]
                compiler = compiler_cls()
                job.stage = stage.value
                job.stage_started_at = utcnow()
                job.progress = round(ran / total_to_run, 4)
                await self._db.commit()

                stage_begin = utcnow()
                result = await compiler.compile(ctx)
                duration_ms = int((utcnow() - stage_begin).total_seconds() * 1000)

                results[stage.value] = result
                ctx.upstream[stage.value] = result.output
                if job.id:
                    await compiler.save_artifact(ctx, result, job.id, duration_ms=duration_ms)
                ran += 1
                job.confidence = result.confidence
                job.progress = round(ran / total_to_run, 4)
                # 每级编译后立即 commit，释放 SQLite 写锁
                await self._db.commit()

                # 完成度计算与 Runtime 落盘前再次确认任务仍归当前 Worker。
        await self._assert_job_execution_active(job)

        # 完成度计算
        calculator = CompletenessCalculator(self._enterprise_id)
        completeness = calculator.calculate(ctx, interview_completion)

        # 业务嵌入

        runtime_output = ctx.upstream.get(Stage.RUNTIME.value)
        if runtime_output is not None:
            runtime_result = self._get_runtime_result(runtime_output)
            embedder = BusinessEmbedder(self._enterprise_id)
            runtime_result = embedder.embed(runtime_result)
            runtime_result = runtime_result.model_copy(update={
                "completeness": completeness.overall
            })
            ctx.upstream[Stage.RUNTIME.value] = runtime_result

        # 持久化 Enterprise Runtime（增量重编译）
        avg_conf = self._avg_confidence(results)
        runtime_to_persist = self._get_runtime_result(ctx.upstream.get(Stage.RUNTIME.value))
        if runtime_to_persist is not None:
            await self._persist_runtime(runtime_to_persist, completeness.overall, avg_conf)

        # P2 字段对齐: stage 保留为最后完成的阶段名（runtime）
        job.status = "completed"
        job.stage = Stage.RUNTIME.value
        job.completeness = completeness.overall
        job.completed_at = utcnow()
        await self._assert_job_execution_active(job)
        await self._db.commit()

        capability_matrix = self._get_capability_matrix(ctx.upstream.get(Stage.CAPABILITY.value))
        gaps = calculator.to_gaps_contract(completeness)
        runtime = self._get_runtime_result(ctx.upstream.get(Stage.RUNTIME.value))

        return {
            "job_id": job.id,
            "runtime": runtime,
            "capability_matrix": capability_matrix,
            "gaps": gaps,
            "completeness": completeness,
            "results": {k: v.discovered_summary for k, v in results.items()},
        }

    def _compute_downstream_stages(self, affected: list[Stage]) -> list[Stage]:
        """计算受影响层级及其所有下游层级。"""
        if not affected:
            return list(self.STAGE_ORDER)
        min_idx = min(self.STAGE_ORDER.index(s) for s in affected if s in self.STAGE_ORDER)
        return self.STAGE_ORDER[min_idx:]

    async def _assert_job_execution_active(self, job: CompilationJob) -> None:
        """确认当前 Pipeline 仍被允许写入该 Job。

        必须从独立会话读取持久化执行权：在终态提交前对 ORM 对象调用 refresh 会
        丢弃尚未提交的 ``completed``/进度变更；而对同一对象先 flush 又可能令迟到
        Worker 覆盖已接管者。独立会话只读取 lease/cancel 状态，不触碰当前事务中的
        待提交业务字段。若当前会话绑定为内存 SQLite，新连接无法访问相同内存库，
        降级为标量查询当前会话。
        """
        bind = self._db.bind
        is_memory_sqlite = bind and str(bind.url).startswith("sqlite") and (":memory:" in str(bind.url) or "mode=memory" in str(bind.url))

        if is_memory_sqlite:
            result = await self._db.execute(
                select(CompilationJob.lease_owner, CompilationJob.cancel_requested)
                .where(CompilationJob.id == job.id)
            )
            state = result.one_or_none()
        else:
            factory = async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=False) if bind else async_session_factory
            async with factory() as state_db:
                result = await state_db.execute(
                    select(CompilationJob.lease_owner, CompilationJob.cancel_requested)
                    .where(CompilationJob.id == job.id)
                )
                state = result.one_or_none()
        if state is None:
            raise RuntimeError("编译任务不存在，当前 Worker 停止写入")
        lease_owner, cancel_requested = state
        if self._expected_worker_id and lease_owner != self._expected_worker_id:
            raise RuntimeError("编译任务租约已被接管，当前 Worker 停止写入")
        if not cancel_requested:
            return
        job.status = "cancelled"
        job.completed_at = utcnow()
        await self._db.commit()
        raise RuntimeError("编译任务已被请求取消")

    async def _get_or_create_job(

        self, job_id: Optional[str], trigger_source: str
    ) -> CompilationJob:
        """获取或创建编译任务。"""
        if job_id:
            job = await self._get_job(job_id)
            if job:
                return job
        job = CompilationJob(
            enterprise_id=self._enterprise_id,
            trigger_source=trigger_source,
            stage=Stage.INFORMATION.value,
            status="running",
            started_at=utcnow(),
        )
        self._db.add(job)
        # 必须提交，而不只是 flush：每个阶段边界的 `_assert_job_execution_active`
        # 用**独立会话**读租约/取消状态，跨会话看不到未提交的 INSERT，会误判
        # "编译任务不存在"而中止；自建 job 路径（job_id 为空）还会表现为
        # compilation_jobs 上 UPDATE 匹配 0 行的 StaleDataError。
        await self._db.commit()
        return job

    async def _get_job(self, job_id: str) -> Optional[CompilationJob]:
        """按 ID 获取编译任务。"""
        result = await self._db.execute(
            select(CompilationJob).where(CompilationJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def _load_latest_completed_artifacts(
        self,
        ctx: CompilationContext,
        *,
        exclude_job_id: str,
    ) -> None:
        """为增量执行补齐最近完成任务中缺失的上游产物。"""
        latest_job_result = await self._db.execute(
            select(CompilationJob.id)
            .where(
                CompilationJob.enterprise_id == self._enterprise_id,
                CompilationJob.status == "completed",
                CompilationJob.id != exclude_job_id,
            )
            .order_by(CompilationJob.completed_at.desc(), CompilationJob.created_at.desc())
            .limit(1)
        )
        latest_job_id = latest_job_result.scalar_one_or_none()
        if not latest_job_id:
            return
        result = await self._db.execute(
            select(CompilationArtifact)
            .where(CompilationArtifact.job_id == latest_job_id)
            .order_by(CompilationArtifact.created_at.desc())
        )
        for artifact in result.scalars():
            if artifact.stage not in ctx.upstream:
                ctx.upstream[artifact.stage] = artifact.output

    async def _load_artifacts(self, ctx: CompilationContext, job_id: str) -> None:

        """加载已有 artifact 到 upstream。"""
        result = await self._db.execute(
            select(CompilationArtifact)
            .where(CompilationArtifact.job_id == job_id)
            .order_by(CompilationArtifact.created_at.desc())
        )
        seen_stages: set[str] = set()
        for artifact in result.scalars():
            if artifact.stage in seen_stages:
                continue
            seen_stages.add(artifact.stage)
            ctx.upstream[artifact.stage] = artifact.output

    def _get_capability_matrix(self, cap_output: Any) -> CapabilityMatrix:
        """从 Capability 级输出提取 CapabilityMatrix。"""
        if cap_output is None:
            return CapabilityMatrix(
                enterprise_id=self._enterprise_id,
                compiled_at=utcnow(),
                confidence=0.0,
            )
        if hasattr(cap_output, "capability_matrix"):
            return cap_output.capability_matrix
        if isinstance(cap_output, dict):
            cm = cap_output.get("capability_matrix", cap_output)
            return CapabilityMatrix(**cm)
        return CapabilityMatrix(
            enterprise_id=self._enterprise_id,
            compiled_at=utcnow(),
            confidence=0.0,
        )

    def _get_runtime_result(self, runtime_output: Any) -> RuntimeCompileResult:
        """从 Runtime 级输出提取 RuntimeCompileResult。"""
        if runtime_output is None:
            return RuntimeCompileResult(
                compiled_at=utcnow(),
                completeness=0.0,
            )
        if hasattr(runtime_output, "runtime"):
            return runtime_output.runtime
        if isinstance(runtime_output, RuntimeCompileResult):
            return runtime_output
        if isinstance(runtime_output, dict):
            rt = runtime_output.get("runtime", runtime_output)
            return RuntimeCompileResult(**rt)
        return RuntimeCompileResult(
            compiled_at=utcnow(),
            completeness=0.0,
        )

    def _avg_confidence(self, results: dict[str, CompilationResult]) -> float:
        """计算各级置信度均值。"""
        if not results:
            return 0.0
        return sum(r.confidence for r in results.values()) / len(results)

    async def _persist_runtime(
        self,
        runtime_result: RuntimeCompileResult,
        completeness_overall: float,
        confidence: float,
    ) -> None:
        """将编译产出的 Runtime 持久化到 enterprise_runtimes 表。

        调用 runtime_store.save_runtime 创建新版本（语义化版本递增）、
        切换激活态、写审计事件。失败时记录日志但不阻断编译流程
        （runtime 数据已保存在 compilation_artifacts 中，可手动恢复）。

        save_runtime 内部会 await db.commit()，调用方需注意 session 状态。
        """
        changelog = f"五级编译完成（置信度={confidence:.2f}, 完成度={completeness_overall:.2f}）"
        try:
            await save_runtime(
                self._db,
                enterprise_id=self._enterprise_id,
                compile_result=runtime_result,
                changelog=changelog,
                change_type="minor",
            )
            logger.info(
                "Runtime 已持久化: enterprise=%s completeness=%.2f",
                self._enterprise_id,
                completeness_overall,
            )
        except ValueError:
            # 版本号冲突 — 用 patch 递增重试
            logger.warning("Runtime 版本号冲突，使用 patch 递增重试")
            try:
                await save_runtime(
                    self._db,
                    enterprise_id=self._enterprise_id,
                    compile_result=runtime_result,
                    changelog=f"五级编译完成（patch 版本，置信度={confidence:.2f}）",
                    change_type="patch",
                )
            except Exception as e:
                logger.error("Runtime 持久化失败（patch 重试）: %s", e, exc_info=True)
        except Exception as e:
            logger.error("Runtime 持久化失败: %s", e, exc_info=True)


async def run_compilation_background(
    enterprise_id: str,
    folder_path: str,
    job_id: Optional[str] = None,
    interview_completion: float = 0.0,
) -> dict[str, Any]:
    """后台执行全量编译（使用独立 session）。

    供 API 层调用，避免阻塞主请求。
    编译失败时更新 job 状态为 "failed" 并记录错误，不向上抛异常
    （后台任务异常如果未捕获会导致 asyncio 打印警告）。
    """
    from app.models.compiler import CompilationJob
    from app.utils.time import utcnow

    try:
        async with async_session_factory() as db:
            pipeline = CompilationPipeline(db, enterprise_id)
            return await pipeline.run_full(
                folder_path, job_id, interview_completion
            )
    except Exception as e:
        logger.error(
            "后台编译失败: enterprise=%s job=%s error=%s",
            enterprise_id, job_id, e, exc_info=True,
        )
        # 更新 job 状态为 failed
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(CompilationJob).where(CompilationJob.id == job_id)
                )
                job = result.scalar_one_or_none()
                if job and job.status != "completed":
                    job.status = "failed"
                    job.error_message = str(e)[:500]
                    job.completed_at = utcnow()
                    await db.commit()
        except Exception as exc:
            # 尽力记录租约释放失败；真正的异常仍由下面的 raise 继续上抛。
            logger.warning("释放编译任务租约失败: %s", exc, exc_info=True)
        raise
