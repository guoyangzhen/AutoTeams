"""Phase 11: 列表端点分页边界验证测试。

验证各 GET 列表端点的 limit/offset Query 参数有 ge/le 边界约束，
防止客户端传入超大 limit（如 999999999）导致一次性加载海量记录（DoS）。

架构审计 Phase 11 新发现：多个列表端点缺少分页或分页参数无边界校验。
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()


class TestPaginationBounds:
    """验证列表端点的 limit/offset Query 边界约束（422 拒绝越界值）。"""

    @pytest.mark.asyncio
    async def test_conversations_rejects_limit_zero(self, authenticated_client):
        """GET /conversations?limit=0 应返回 422（ge=1）。"""
        resp = await authenticated_client.get("/api/v1/conversations?limit=0")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_conversations_rejects_limit_over_max(self, authenticated_client):
        """GET /conversations?limit=501 应返回 422（le=500）。"""
        resp = await authenticated_client.get("/api/v1/conversations?limit=501")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_conversations_rejects_offset_negative(self, authenticated_client):
        """GET /conversations?offset=-1 应返回 422（ge=0）。"""
        resp = await authenticated_client.get("/api/v1/conversations?offset=-1")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_conversations_accepts_valid_pagination(self, authenticated_client):
        """GET /conversations?limit=100&offset=0 应通过（200）。"""
        resp = await authenticated_client.get("/api/v1/conversations?limit=100&offset=0")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_process_rejects_limit_zero(self, authenticated_client):
        """GET /process?limit=0 应返回 422（ge=1）。"""
        resp = await authenticated_client.get("/api/v1/process?limit=0")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_process_rejects_limit_over_max(self, authenticated_client):
        """GET /process?limit=201 应返回 422（le=200）。"""
        resp = await authenticated_client.get("/api/v1/process?limit=201")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_process_accepts_valid_pagination(self, authenticated_client):
        """GET /process?limit=50&offset=0 应通过（200）。"""
        resp = await authenticated_client.get("/api/v1/process?limit=50&offset=0")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_skills_rejects_limit_zero(self, authenticated_client):
        """GET /skills?limit=0 应返回 422（ge=1）。"""
        resp = await authenticated_client.get("/api/v1/skills?limit=0")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_skills_rejects_limit_over_max(self, authenticated_client):
        """GET /skills?limit=501 应返回 422（le=500）。"""
        resp = await authenticated_client.get("/api/v1/skills?limit=501")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_skills_accepts_valid_pagination(self, authenticated_client):
        """GET /skills?limit=200&offset=0 应通过（200）。"""
        resp = await authenticated_client.get("/api/v1/skills?limit=200&offset=0")
        assert resp.status_code == 200
