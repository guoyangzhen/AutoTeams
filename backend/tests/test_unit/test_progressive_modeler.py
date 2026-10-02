"""渐进式重编译必须提交可执行的耐久 CompilationJob。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.cognition import progressive_modeler
from app.services.cognition.progressive_modeler import ProgressiveModeler


ENTERPRISE_ID = "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_trigger_recompile_enqueues_explicit_validated_snapshot(monkeypatch):
    db = AsyncMock()
    # 实现会把 job.stage 与 affected_stages[0] 保持同步，替身必须提供该字段
    job = SimpleNamespace(
        id="job-1",
        stage=None,
        affected_stages=None,
        trigger_source="file_change",
    )
    enqueue = AsyncMock(return_value=(job, True))
    monkeypatch.setattr(progressive_modeler, "enqueue_compilation_job", enqueue)

    result = await ProgressiveModeler(db, ENTERPRISE_ID).trigger_recompile(
        "file_change",
        folder_path="/safe/upload/snapshot",
    )

    assert result is job
    enqueue.assert_awaited_once_with(
        db,
        enterprise_id=ENTERPRISE_ID,
        folder_path="/safe/upload/snapshot",
        trigger_source="file_change",
    )
    assert job.affected_stages == "information,knowledge"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_trigger_recompile_reuses_only_latest_completed_snapshot(monkeypatch):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: "/safe/last-completed")
    job = SimpleNamespace(
        id="job-2",
        stage=None,
        affected_stages="information",
        trigger_source="interview_answer",
    )
    enqueue = AsyncMock(return_value=(job, False))
    monkeypatch.setattr(progressive_modeler, "enqueue_compilation_job", enqueue)

    result = await ProgressiveModeler(db, ENTERPRISE_ID).trigger_recompile(
        "interview_answer",
        affected_stages=["knowledge", "process"],
    )

    assert result is job
    enqueue.assert_awaited_once_with(
        db,
        enterprise_id=ENTERPRISE_ID,
        folder_path="/safe/last-completed",
        trigger_source="interview_answer",
    )
    assert job.affected_stages == "information,knowledge,process"


@pytest.mark.asyncio
async def test_trigger_recompile_rejects_missing_executable_input(monkeypatch):
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    enqueue = AsyncMock()
    monkeypatch.setattr(progressive_modeler, "enqueue_compilation_job", enqueue)

    with pytest.raises(ValueError, match="recompile_requires_folder_path_or_completed_compilation"):
        await ProgressiveModeler(db, ENTERPRISE_ID).trigger_recompile("file_change")

    enqueue.assert_not_awaited()


@pytest.mark.parametrize("status", ["queued", "cancelled", "pending", "completed"])
def test_compilation_job_response_accepts_full_durable_lifecycle(status):
    from app.schemas.compiler import CompilationJobResponse

    response = CompilationJobResponse(
        job_id="job-1",
        enterprise_id=ENTERPRISE_ID,
        stage="information",
        status=status,
    )

    assert response.status == status
