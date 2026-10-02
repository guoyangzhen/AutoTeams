"""编译流水线端到端测试（PRD §5.2 编译交互流程）。

测试五级编译器串联 + 完成度计算 + 业务嵌入。
LLM 调用全部 mock。
"""
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from app.services.compiler.pipeline import CompilationPipeline
from app.services.compiler.base import Stage


@pytest.mark.asyncio
async def test_pipeline_empty_folder(db_session, tmp_path):
    """测试空文件夹的端到端编译。"""
    pipeline = CompilationPipeline(db_session, "ent_pipe_1")
    result = await pipeline.run_full(str(tmp_path))

    assert result["job_id"] is not None
    assert result["runtime"] is not None
    assert result["capability_matrix"] is not None
    assert result["gaps"] is not None
    assert result["completeness"] is not None
    # 空文件夹应产生低完成度
    assert result["completeness"].overall < 0.5
    assert result["completeness"].level == "incomplete"


@pytest.mark.asyncio
async def test_pipeline_with_files(db_session, upload_root):
    """测试有文件的端到端编译（LLM mock）。"""
    # 在 upload_root 下创建测试文件
    root_path = Path(upload_root)
    (root_path / "org.md").write_text(
        "# 组织架构\n销售部负责产品销售，采购部负责物料采购。\n"
        "销售经理负责管理销售团队。",
        encoding="utf-8",
    )
    (root_path / "process.md").write_text(
        "# 采购流程\n1. 提交采购申请\n2. 部门经理审批\n3. 财务确认",
        encoding="utf-8",
    )

    # Mock LLM：NER 返回实体，SOP 返回步骤，关系返回空
    ner_response = '''[
        {"name": "销售部", "entity_type": "Department", "attributes": {"description": "负责销售"}},
        {"name": "采购部", "entity_type": "Department", "attributes": {"description": "负责采购"}},
        {"name": "销售经理", "entity_type": "Role", "attributes": {"department": "销售部", "level": "L2"}},
        {"name": "采购流程", "entity_type": "Process", "attributes": {"type": "approval", "description": "采购审批流程"}}
    ]'''
    sop_response = '''[
        {"name": "提交采购申请", "order": 1, "approval_required": false},
        {"name": "部门经理审批", "order": 2, "approval_required": true},
        {"name": "财务确认", "order": 3, "approval_required": true}
    ]'''

    pipeline = CompilationPipeline(db_session, "ent_pipe_2")

    call_count = [0]
    async def mock_chat_fn(messages, **kwargs):
        call_count[0] += 1
        # 第一次调用是 Information 级 NER
        if "NER" in messages[0]["content"] or "命名实体" in messages[0]["content"] or "信息抽取" in messages[0]["content"]:
            return ner_response
        # Process 级 SOP
        if "SOP" in messages[0]["content"] or "标准操作流程" in messages[0]["content"]:
            return sop_response
        # 关系抽取
        return "[]"

    with patch("app.services.llm_service.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = mock_chat_fn
        result = await pipeline.run_full(str(upload_root))

    assert result["job_id"] is not None
    assert result["runtime"] is not None
    # 应有编译产物
    assert len(result["capability_matrix"].positions) > 0
    assert len(result["runtime"].agents) > 0


@pytest.mark.asyncio
async def test_pipeline_downstream_stages(db_session):
    """测试增量重编译的下游层级计算。"""
    pipeline = CompilationPipeline(db_session, "ent_pipe_3")
    # file_upload 影响 information + knowledge
    # 下游应包含 information, knowledge, process, capability, runtime
    stages = pipeline._compute_downstream_stages([Stage.INFORMATION])
    assert Stage.INFORMATION in stages
    assert Stage.KNOWLEDGE in stages
    assert Stage.RUNTIME in stages

    # process 影响 process + capability + runtime
    stages = pipeline._compute_downstream_stages([Stage.PROCESS])
    assert Stage.PROCESS in stages
    assert Stage.CAPABILITY in stages
    assert Stage.RUNTIME in stages
    assert Stage.INFORMATION not in stages


@pytest.mark.asyncio
async def test_pipeline_results_summaries(db_session, tmp_path):
    """测试每级编译产出包含 discovered_summary。"""
    pipeline = CompilationPipeline(db_session, "ent_pipe_4")
    result = await pipeline.run_full(str(tmp_path))

    # results 应包含每级的摘要
    assert "results" in result
    # 至少 information 级有摘要
    assert "information" in result["results"]
