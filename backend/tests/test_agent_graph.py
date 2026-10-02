"""B2: LangGraph approval 启发式自动审批与双轨构建统一测试。

覆盖：
1. approval_node 低风险自动通过（require_approval=True + 文件≤50 + ≤100MB + 无敏感扩展名）
2. approval_node 高风险触发 interrupt（敏感扩展名 / 文件数超限 / 总大小超限）
3. BuildAgentViaGraphRequest 默认 require_approval=False
4. _evaluate_approval_heuristic 启发式判定函数边界

验收对应：
- 验收标准 1：默认 require_approval=False 时直接通过
- 验收标准 2：启用审批时低风险自动通过、高风险才 interrupt
"""
import pytest

from app.schemas.agent import BuildAgentViaGraphRequest
from app.services.agent_graph import (
    AgentBuildState,
    _evaluate_approval_heuristic,
    _AUTO_APPROVE_MAX_FILES,
    _AUTO_APPROVE_MAX_SIZE_BYTES,
    _SENSITIVE_EXTENSIONS,
    approval_node,
)
from app.services.folder_scanner import ScannedFile


def _make_file(name: str, size: int = 1024, extension: str = ".txt", file_type: str = "document") -> ScannedFile:
    """构造测试用 ScannedFile。"""
    return ScannedFile(
        name=name,
        path=f"/tmp/{name}",
        size=size,
        file_type=file_type,
        extension=extension,
    )


# ============================================================
# 1. approval_node 自动通过 / interrupt 测试
# ============================================================

class TestApprovalAutoPassLowRisk:
    """验收标准 2（低风险自动通过）。"""

    @pytest.mark.asyncio
    async def test_approval_auto_pass_low_risk(self):
        """require_approval=True + 低风险（少量普通文件）应自动通过，不触发 interrupt。"""
        files = [_make_file(f"doc{i}.txt", size=10 * 1024) for i in range(5)]
        state: AgentBuildState = {
            "require_approval": True,
            "files": files,
            "file_types": {"document": 5},
            "messages": [],
        }

        result = await approval_node(state)

        assert result["current_step"] == "approval"
        # 自动通过 message 包含「启发式自动通过」
        assert any("启发式自动通过" in m for m in result["messages"])
        # 不应标记 failed
        assert result.get("status") != "failed"

    @pytest.mark.asyncio
    async def test_approval_auto_pass_empty_files(self):
        """require_approval=True + 空文件列表（0 文件）应自动通过。"""
        state: AgentBuildState = {
            "require_approval": True,
            "files": [],
            "file_types": {},
            "messages": [],
        }

        result = await approval_node(state)
        assert result["current_step"] == "approval"
        assert any("启发式自动通过" in m for m in result["messages"])

    @pytest.mark.asyncio
    async def test_approval_skipped_when_not_required(self):
        """验收标准 1：require_approval=False 时直接通过（默认路径）。"""
        state: AgentBuildState = {
            "require_approval": False,
            "files": [_make_file("any.env", extension=".env")],
            "messages": [],
        }

        result = await approval_node(state)
        assert result["current_step"] == "approval"
        assert "直接通过" in result["messages"][0]
        # 即便含敏感扩展名，未启用审批也不应 interrupt
        assert "启发式" not in result["messages"][0]


class TestApprovalInterruptHighRisk:
    """验收标准 2（高风险 interrupt）。"""

    @pytest.mark.asyncio
    async def test_approval_interrupt_sensitive_extension(self):
        """含敏感扩展名（.env）应触发 interrupt。"""
        files = [_make_file("secrets.env", extension=".env")]
        state: AgentBuildState = {
            "require_approval": True,
            "files": files,
            "file_types": {"config": 1},
            "messages": [],
        }

        with pytest.raises(Exception):
            await approval_node(state)

    @pytest.mark.asyncio
    async def test_approval_interrupt_too_many_files(self):
        """文件数超过阈值（>50）应触发 interrupt。"""
        files = [_make_file(f"f{i}.txt", size=100) for i in range(_AUTO_APPROVE_MAX_FILES + 1)]
        state: AgentBuildState = {
            "require_approval": True,
            "files": files,
            "file_types": {"document": len(files)},
            "messages": [],
        }

        with pytest.raises(Exception):
            await approval_node(state)

    @pytest.mark.asyncio
    async def test_approval_interrupt_oversize(self):
        """文件总大小超过 100MB 应触发 interrupt。"""
        # 单个文件超过 100MB
        files = [_make_file("big.bin", size=_AUTO_APPROVE_MAX_SIZE_BYTES + 1, extension=".bin", file_type="other")]
        state: AgentBuildState = {
            "require_approval": True,
            "files": files,
            "file_types": {"other": 1},
            "messages": [],
        }

        with pytest.raises(Exception):
            await approval_node(state)


# ============================================================
# 2. 默认 require_approval=False 测试
# ============================================================

class TestDefaultRequireApprovalFalse:
    """验收标准 1 / O-06：双轨构建默认 require_approval=False。"""

    def test_default_require_approval_false(self):
        """BuildAgentViaGraphRequest 不传 require_approval 时默认为 False。"""
        req = BuildAgentViaGraphRequest(
            enterprise_id="ent-1",
            name="test-agent",
            folder_path="/tmp/docs",
        )
        assert req.require_approval is False

    def test_require_approval_explicit_true(self):
        """显式传 require_approval=True 应被尊重。"""
        req = BuildAgentViaGraphRequest(
            enterprise_id="ent-1",
            name="test-agent",
            folder_path="/tmp/docs",
            require_approval=True,
        )
        assert req.require_approval is True


# ============================================================
# 3. _evaluate_approval_heuristic 启发式判定函数边界测试
# ============================================================

class TestEvaluateApprovalHeuristic:
    """启发式判定函数单元测试。"""

    def test_low_risk_pass(self):
        """少量普通文件应判为可自动通过。"""
        files = [_make_file(f"d{i}.txt", size=1024) for i in range(10)]
        can_pass, reason = _evaluate_approval_heuristic(files)
        assert can_pass is True
        assert "低风险" in reason

    def test_sensitive_extension_blocked(self):
        """敏感扩展名应被阻断。"""
        for ext in _SENSITIVE_EXTENSIONS:
            files = [_make_file(f"secret{ext}", extension=ext)]
            can_pass, reason = _evaluate_approval_heuristic(files)
            assert can_pass is False
            assert "敏感扩展名" in reason

    def test_file_count_threshold_blocked(self):
        """文件数超过阈值应被阻断。"""
        files = [_make_file(f"f{i}.txt", size=100) for i in range(_AUTO_APPROVE_MAX_FILES + 1)]
        can_pass, reason = _evaluate_approval_heuristic(files)
        assert can_pass is False
        assert "文件数量" in reason

    def test_size_threshold_blocked(self):
        """总大小超过阈值应被阻断。"""
        files = [_make_file("big.bin", size=_AUTO_APPROVE_MAX_SIZE_BYTES + 1, extension=".bin", file_type="other")]
        can_pass, reason = _evaluate_approval_heuristic(files)
        assert can_pass is False
        assert "文件总大小" in reason

    def test_boundary_exact_threshold_pass(self):
        """边界值：恰好等于阈值应自动通过（<=）。"""
        # 恰好 50 个文件，每个 1KB，总 50KB
        files = [_make_file(f"f{i}.txt", size=1024) for i in range(_AUTO_APPROVE_MAX_FILES)]
        can_pass, reason = _evaluate_approval_heuristic(files)
        assert can_pass is True

    def test_boundary_exact_size_pass(self):
        """边界值：总大小恰好 100MB 应自动通过（<=）。"""
        files = [_make_file("exact.bin", size=_AUTO_APPROVE_MAX_SIZE_BYTES, extension=".bin", file_type="other")]
        can_pass, reason = _evaluate_approval_heuristic(files)
        assert can_pass is True
