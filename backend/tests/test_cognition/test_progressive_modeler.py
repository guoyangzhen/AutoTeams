"""渐进式建模测试（PRD §5.1）。"""
import pytest
from unittest.mock import AsyncMock

from app.services.cognition.progressive_modeler import ProgressiveModeler
from app.models.compiler import CompilationJob


@pytest.mark.asyncio
async def test_analyze_affected_stages(db_session):
    """测试受影响层级分析。"""
    modeler = ProgressiveModeler(db_session, "ent_prog_1")

    # 文件上传 → information + knowledge
    stages = modeler.analyze_affected_stages("file_upload")
    assert "information" in stages
    assert "knowledge" in stages

    # SOP 修改 → process + capability + runtime
    stages = modeler.analyze_affected_stages("sop_change")
    assert "process" in stages
    assert "capability" in stages
    assert "runtime" in stages

    # 未知触发源 → 默认 information
    stages = modeler.analyze_affected_stages("unknown_trigger")
    assert "information" in stages


@pytest.mark.asyncio
async def test_trigger_recompile(db_session):
    """测试触发增量重编译。"""
    modeler = ProgressiveModeler(db_session, "ent_prog_2")
    job = await modeler.trigger_recompile("file_upload", folder_path="/uploads/ent_prog_2")

    assert job.id is not None
    assert job.enterprise_id == "ent_prog_2"
    assert job.trigger_source == "file_upload"
    assert job.status in ("pending", "queued")
    assert job.stage == "information"
    assert "information" in job.affected_stages
    assert "knowledge" in job.affected_stages


@pytest.mark.asyncio
async def test_trigger_recompile_explicit_stages(db_session):
    """测试显式指定受影响层级。"""
    modeler = ProgressiveModeler(db_session, "ent_prog_3")
    job = await modeler.trigger_recompile(
        "sop_change",
        affected_stages=["process", "capability"],
        folder_path="/uploads/ent_prog_3",
    )
    assert job.affected_stages == "process,capability"
    assert job.stage == "process"


@pytest.mark.asyncio
async def test_generate_user_feedback(db_session):
    """测试用户反馈生成。"""
    modeler = ProgressiveModeler(db_session, "ent_prog_4")
    job = await modeler.trigger_recompile("file_upload", folder_path="/uploads/ent_prog_4")

    feedback = modeler.generate_user_feedback(job)
    assert "信息" in feedback
    assert "知识" in feedback


@pytest.mark.asyncio
async def test_trigger_recompile_rejects_missing_executable_input(db_session):
    """既无 folder_path 又无历史成功编译时必须显式失败，而不是入队空任务。

    空路径入队会让 worker 领走一个永远扫不到文件的任务，最后以
    "成功但零产物"收场 —— 比直接报错更难排查。
    """
    modeler = ProgressiveModeler(db_session, "ent_prog_no_input")
    with pytest.raises(ValueError, match="recompile_requires_folder_path_or_completed_compilation"):
        await modeler.trigger_recompile("file_upload")
