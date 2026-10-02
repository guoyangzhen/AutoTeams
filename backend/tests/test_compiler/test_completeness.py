"""完成度评估测试（PRD §4.3 + §4.4）。

测试 5 维加权计算 + 等级映射 + 缺失项建议 + CompilationGaps 契约（spec.md §10.4）。
"""
import pytest
from datetime import datetime

from app.services.compiler.completeness import CompletenessCalculator
from app.services.compiler.base import CompilationContext
from app.schemas.compiler import (
    InformationCompileOutput,
    InformationEntry,
    KnowledgeCompileOutput,
    ProcessCompileOutput,
    ProcessDefinition,
    CapabilityCompileOutput,
    CapabilityMatrix,
    PositionCapability,
    RuntimeCompileOutput,
    RuntimeCompileResult,
)
from app.utils.time import utcnow


def _make_full_upstream(enterprise_id="ent_comp"):
    """构造完整的上游产物。"""
    info = InformationCompileOutput(
        enterprise_id=enterprise_id,
        entries=[InformationEntry(entry_id="e1", entry_type="Department", name="销售部", confidence=0.8)],
        total_files=5,
        confidence=0.85,
    )
    knowledge = KnowledgeCompileOutput(
        enterprise_id=enterprise_id,
        nodes=[],
        edges=[],
        vector_collection=f"enterprise_{enterprise_id}",
        confidence=0.8,
    )
    process = ProcessCompileOutput(
        enterprise_id=enterprise_id,
        processes=[
            ProcessDefinition(process_id="p1", name="采购流程", confidence=0.7),
            ProcessDefinition(process_id="p2", name="销售流程", confidence=0.7),
            ProcessDefinition(process_id="p3", name="财务审批流程", confidence=0.7),
        ],
        confidence=0.75,
    )
    capability = CapabilityCompileOutput(
        enterprise_id=enterprise_id,
        capability_matrix=CapabilityMatrix(
            enterprise_id=enterprise_id,
            positions=[PositionCapability(
                position_id="r1", position_name="经理",
                required_skills=[],
                priority="P1",
            )],
            compiled_at=utcnow(),
            confidence=0.8,
        ),
    )
    runtime = RuntimeCompileOutput(
        enterprise_id=enterprise_id,
        runtime=RuntimeCompileResult(compiled_at=utcnow(), completeness=0.7),
    )
    return {
        "information": info,
        "knowledge": knowledge,
        "process": process,
        "capability": capability,
        "runtime": runtime,
    }


def test_completeness_weights():
    """验证 5 维权重总和为 1.0。"""
    calc = CompletenessCalculator("ent_test")
    total = sum(calc.WEIGHTS.values())
    assert abs(total - 1.0) < 0.001


def test_completeness_full_upstream():
    """测试完整上游产物的完成度计算。"""
    upstream = _make_full_upstream()
    ctx = CompilationContext(enterprise_id="ent_comp", db=None, upstream=upstream)
    calc = CompletenessCalculator("ent_comp")
    result = calc.calculate(ctx, interview_completion=0.5)

    assert result.overall > 0
    assert "data_coverage" in result.dimensions
    assert "process_coverage" in result.dimensions
    assert "role_coverage" in result.dimensions
    assert "confidence" in result.dimensions
    assert "interview_completion" in result.dimensions


def test_completeness_level_mapping():
    """测试等级映射（PRD §4.3：≥80% runnable / 60-79% basic / <60% incomplete）。"""
    calc = CompletenessCalculator("ent_test")
    assert calc._map_level(0.9) == "runnable"
    assert calc._map_level(0.8) == "runnable"
    assert calc._map_level(0.79) == "basic"
    assert calc._map_level(0.6) == "basic"
    assert calc._map_level(0.59) == "incomplete"
    assert calc._map_level(0.3) == "incomplete"


def test_completeness_empty_upstream():
    """测试空上游产物的完成度。"""
    ctx = CompilationContext(enterprise_id="ent_empty", db=None, upstream={})
    calc = CompletenessCalculator("ent_empty")
    result = calc.calculate(ctx)

    assert result.overall < 0.5
    assert result.level == "incomplete"
    # 应有缺失项建议
    assert len(result.gaps) > 0


def test_completeness_gaps_contract():
    """验证 CompilationGaps 契约（spec.md §10.4）。"""
    upstream = _make_full_upstream()
    ctx = CompilationContext(enterprise_id="ent_gaps", db=None, upstream={})
    calc = CompletenessCalculator("ent_gaps")
    result = calc.calculate(ctx)
    gaps = calc.to_gaps_contract(result)

    # 契约字段验证
    assert hasattr(gaps, "enterprise_id")
    assert hasattr(gaps, "overall_completeness")
    assert hasattr(gaps, "dimension_scores")
    assert hasattr(gaps, "gaps")
    assert hasattr(gaps, "compiled_at")
    assert gaps.enterprise_id == "ent_gaps"
    assert isinstance(gaps.compiled_at, datetime)
    assert isinstance(gaps.gaps, list)


def test_completeness_weighted_formula():
    """验证 5 维加权公式正确性。"""
    upstream = _make_full_upstream()
    ctx = CompilationContext(enterprise_id="ent_formula", db=None, upstream=upstream)
    calc = CompletenessCalculator("ent_formula")
    result = calc.calculate(ctx, interview_completion=1.0)

    # 手动计算验证
    expected = (
        result.dimensions["data_coverage"] * 0.30
        + result.dimensions["process_coverage"] * 0.25
        + result.dimensions["role_coverage"] * 0.20
        + result.dimensions["confidence"] * 0.15
        + result.dimensions["interview_completion"] * 0.10
    )
    assert abs(result.overall - round(expected, 4)) < 0.01


def test_completeness_gaps_identification():
    """测试缺失项识别。"""
    ctx = CompilationContext(enterprise_id="ent_gaps2", db=None, upstream={})
    calc = CompletenessCalculator("ent_gaps2")
    result = calc.calculate(ctx)

    # 空上游应识别出多个缺失项
    gap_types = [g.gap_type for g in result.gaps]
    assert "data" in gap_types
    assert "process" in gap_types
    assert "role" in gap_types

    # 每个缺失项应有描述和建议
    for gap in result.gaps:
        assert gap.description
        assert gap.suggestion
        assert gap.impact_on_completeness >= 0
