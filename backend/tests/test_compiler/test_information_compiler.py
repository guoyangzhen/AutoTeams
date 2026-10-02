"""Information Compiler 测试（PRD §4.3 第一级）。

测试文件解析 + NER + 结构化输出 + 置信度。
LLM 调用全部 mock。
"""
import os
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from app.services.compiler.information_compiler import InformationCompiler
from app.services.compiler.base import CompilationContext


@pytest.mark.asyncio
async def test_information_compiler_no_folder(db_session, tmp_path):
    """测试空文件夹编译。"""
    compiler = InformationCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_info_1",
        db=db_session,
        folder_path=str(tmp_path),
    )
    result = await compiler.compile(ctx)

    assert result.stage == "information"
    assert result.confidence > 0
    assert result.output.enterprise_id == "ent_info_1"
    assert result.output.total_files == 0


@pytest.mark.asyncio
async def test_information_compiler_with_files(db_session, upload_root):
    """测试有文件的编译（LLM mock）。"""
    # 在 upload_root 下创建测试文件
    root_path = Path(upload_root)
    (root_path / "sales.md").write_text("# 销售流程\n销售部负责产品推广和客户关系管理。", encoding="utf-8")
    (root_path / "roles.csv").write_text("name,department,level\n张三,销售部,L2\n李四,财务部,L1", encoding="utf-8")

    # Mock LLM 返回 NER 结果
    llm_response = '[{"name": "销售部", "entity_type": "Department", "attributes": {"description": "负责销售"}}, {"name": "销售经理", "entity_type": "Role", "attributes": {"department": "销售部"}}]'

    compiler = InformationCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_info_2",
        db=db_session,
        folder_path=str(upload_root),
    )

    with patch("app.services.compiler.information_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = llm_response
        result = await compiler.compile(ctx)

    assert result.stage == "information"
    assert result.output.total_files >= 2
    assert len(result.output.entries) > 0
    assert result.confidence > 0
    # 验证 mock 被调用
    assert mock_chat.called


@pytest.mark.asyncio
async def test_information_compiler_llm_failure_fallback(db_session, upload_root):
    """测试 LLM 失败时的降级处理。"""
    root_path = Path(upload_root)
    (root_path / "test.md").write_text("# 测试文档\n这是一个测试文档内容。", encoding="utf-8")

    compiler = InformationCompiler()
    ctx = CompilationContext(
        enterprise_id="ent_info_3",
        db=db_session,
        folder_path=str(upload_root),
    )

    with patch("app.services.compiler.information_compiler.llm_service.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.side_effect = Exception("LLM 服务不可用")
        result = await compiler.compile(ctx)

    # 降级：基于文件名生成基础条目
    assert result.stage == "information"
    assert len(result.output.entries) > 0
    assert result.output.entries[0].confidence == 0.3  # 降级置信度


@pytest.mark.asyncio
async def test_information_compiler_confidence_calculation():
    """测试置信度计算。"""
    compiler = InformationCompiler()
    # 0 个文件 → base
    assert compiler.calculate_confidence(0, 0) == 0.3
    # 全部解析成功 → base + max_bonus
    assert compiler.calculate_confidence(10, 10) == 1.0
    # 部分成功
    conf = compiler.calculate_confidence(10, 5)
    assert 0.3 < conf < 1.0
