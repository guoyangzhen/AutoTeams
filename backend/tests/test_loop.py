"""反馈循环 API 测试。

覆盖：
1. GET /loop/{agent_id}/status - 获取循环状态
2. GET /loop/{agent_id}/insights - 获取聚合洞察（含反馈分析、知识缺口）
3. R5: 已废弃的 /feedback、/knowledge-gaps、/rag-evaluation 端点应返回 404
4. 权限校验
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()


class TestLoopApi:
    """反馈循环 API 测试。"""

    @pytest.mark.asyncio
    async def test_get_loop_status_nonexistent_agent(self, enterprise_authenticated_client):
        """获取不存在 Agent 的循环状态应返回 404。"""
        resp = await enterprise_authenticated_client.get("/api/v1/loop/nonexistent-agent-id/status")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_loop_insights_nonexistent_agent(self, enterprise_authenticated_client):
        """获取不存在 Agent 的聚合洞察应返回 404。"""
        resp = await enterprise_authenticated_client.get("/api/v1/loop/nonexistent-agent-id/insights")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deprecated_feedback_endpoint_returns_404(self, authenticated_client):
        """R5: /feedback 已硬删除，应返回 404。"""
        resp = await authenticated_client.get("/api/v1/loop/nonexistent-agent-id/feedback")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deprecated_knowledge_gaps_endpoint_returns_404(self, authenticated_client):
        """R5: /knowledge-gaps 已硬删除，应返回 404。"""
        resp = await authenticated_client.get("/api/v1/loop/nonexistent-agent-id/knowledge-gaps")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_deprecated_rag_evaluation_endpoint_returns_404(self, authenticated_client):
        """R5: /rag-evaluation 已硬删除，应返回 404。"""
        resp = await authenticated_client.get("/api/v1/loop/nonexistent-agent-id/rag-evaluation")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthorized_access(self, client):
        """未认证访问循环端点应返回 401/403。"""
        resp = await client.get("/api/v1/loop/nonexistent-agent-id/status")
        assert resp.status_code in (401, 403)
