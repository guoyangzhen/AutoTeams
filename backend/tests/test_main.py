"""测试 app/main.py 中的中间件、异常处理器与生命周期钩子。"""
import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


class TestRequestIdMiddleware:
    """P3-1: request_id 中间件测试。"""

    @pytest.mark.asyncio
    async def test_request_id_passed_through_header(self, client):
        """客户端传入 X-Request-Id 时应被透传。"""
        resp = await client.get("/health/live", headers={"X-Request-Id": "req-123"})
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "req-123"

    @pytest.mark.asyncio
    async def test_request_id_generated_when_missing(self, client):
        """未传入 X-Request-Id 时后端应自动生成。"""
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) > 0


class TestExceptionHandlers:
    """P1-10: 全局异常处理器测试。"""

    @pytest.mark.asyncio
    async def test_validation_error_returns_unified_format(self, client):
        """参数验证失败应返回统一 422 格式。"""
        resp = await client.post("/api/v1/auth/register", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert body["message"] == "请求参数验证失败"

    @pytest.mark.asyncio
    async def test_http_exception_returns_unified_format(self, client):
        """StarletteHTTPException 应返回统一格式并带 request_id。"""
        from app.main import http_exception_handler

        request = Request(scope={
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        })
        exc = StarletteHTTPException(status_code=403, detail="FORBIDDEN")
        response = await http_exception_handler(request, exc)
        assert response.status_code == 403
        body = response.body.decode()
        assert '"success":false' in body
        assert "FORBIDDEN" in body


class TestMetricsEndpoint:
    """P1-06-E + BE-SEC-05: /metrics 端点测试。"""

    @pytest.mark.asyncio
    async def test_metrics_default_returns_prometheus(self, client, monkeypatch):
        """未配置 METRICS_AUTH_TOKEN 时 /metrics 返回 Prometheus 指标。"""
        monkeypatch.setattr("app.main.settings.METRICS_AUTH_TOKEN", "")
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "autoteams_http_requests_total" in resp.text

    @pytest.mark.asyncio
    async def test_metrics_with_bearer_token(self, client, monkeypatch):
        """通过 Authorization: Bearer <token> 访问 /metrics。"""
        monkeypatch.setattr("app.main.settings.METRICS_AUTH_TOKEN", "tok")
        resp = await client.get("/metrics", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 200


class TestLifespan:
    """应用生命周期钩子测试。"""

    @pytest.mark.asyncio
    async def test_lifespan_yields_and_disposes(self):
        """lifespan 上下文管理器应能正常进入和退出。"""
        from app.main import lifespan, app

        async with lifespan(app):
            pass
        # 正常退出不抛异常即通过

    @pytest.mark.asyncio
    async def test_lifespan_metrics_warning_in_production(self, monkeypatch, caplog):
        """BE-SEC-05: 生产环境未配置 METRICS_AUTH_TOKEN 时应在日志中告警。"""
        from app.main import lifespan, app

        monkeypatch.setattr("app.main.settings.DEBUG", False)
        monkeypatch.setattr("app.main.settings.METRICS_AUTH_TOKEN", "")

        with caplog.at_level("WARNING"):
            async with lifespan(app):
                pass

        assert any("METRICS_AUTH_TOKEN" in record.message for record in caplog.records)
