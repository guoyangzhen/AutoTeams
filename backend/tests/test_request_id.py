"""P3-1: request_id 透传测试。"""
import pytest


class TestRequestId:
    """验证 X-Request-Id 在成功与异常响应中均正确返回。"""

    @pytest.mark.asyncio
    async def test_health_returns_request_id(self, client):
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 32  # UUID4 hex

    @pytest.mark.asyncio
    async def test_request_id_inherited_from_client(self, client):
        custom_id = "custom-request-id-123"
        resp = await client.get("/health/live", headers={"X-Request-Id": custom_id})
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] == custom_id

    @pytest.mark.asyncio
    async def test_error_response_returns_request_id(self, client):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code in (401, 403)
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 32
