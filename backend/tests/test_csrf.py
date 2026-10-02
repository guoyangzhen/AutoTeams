"""BE-SEC-01: CSRF Double Submit Cookie 防护测试。"""
import pytest


async def _register_and_login(client) -> str:
    """辅助：注册用户并登录，返回 csrf_token cookie 值。"""
    await client.post("/api/v1/auth/register", json={
        "email": "csrf-user@test.com",
        "name": "CSRF 测试用户",
        "password": "pass1234",
    })
    csrf_token = client.cookies.get("csrf_token")
    assert csrf_token, "登录后应下发 csrf_token cookie"
    return csrf_token


class TestCSRFProtection:
    """验证业务端点的状态变更请求需要正确的 CSRF token。"""

    @pytest.mark.asyncio
    async def test_business_post_without_csrf_token_rejected(self, raw_client):
        """已登录用户向业务端点发起 POST 但缺少 X-CSRF-Token header 时应返回 403。"""
        await _register_and_login(raw_client)
        # 模拟攻击者无法读取 cookie 的场景：清空 csrf cookie，保留 access cookie
        raw_client.cookies.pop("csrf_token", None)
        resp = await raw_client.post("/api/v1/enterprises", json={"name": "恶意企业"})
        assert resp.status_code == 403
        assert resp.json()["message"] == "CSRF token missing or invalid"

    @pytest.mark.asyncio
    async def test_business_post_with_wrong_csrf_token_rejected(self, raw_client):
        """X-CSRF-Token header 与 cookie 不一致时应返回 403。"""
        await _register_and_login(raw_client)
        resp = await raw_client.post(
            "/api/v1/enterprises",
            json={"name": "恶意企业"},
            headers={"X-CSRF-Token": "attacker-token"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_business_post_with_valid_csrf_token_allowed(self, raw_client):
        """携带正确的 X-CSRF-Token header 时状态变更请求应成功。"""
        csrf_token = await _register_and_login(raw_client)
        resp = await raw_client.post(
            "/api/v1/enterprises",
            json={"name": "合法企业"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "合法企业"

    @pytest.mark.asyncio
    async def test_get_request_not_require_csrf(self, raw_client):
        """GET 业务请求不需要 CSRF token。"""
        await _register_and_login(raw_client)
        # 先创建一个企业
        csrf_token = raw_client.cookies.get("csrf_token")
        await raw_client.post(
            "/api/v1/enterprises",
            json={"name": "GET 测试企业"},
            headers={"X-CSRF-Token": csrf_token},
        )
        # 清空 csrf cookie 也不影响 GET
        raw_client.cookies.pop("csrf_token", None)
        resp = await raw_client.get("/api/v1/auth/me")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_endpoints_exempt_from_csrf(self, raw_client):
        """认证端点豁免 CSRF，登录/注册等不受 CSRF 中间件拦截。"""
        resp = await raw_client.post("/api/v1/auth/login", json={
            "email": "nobody@test.com",
            "password": "anypass",
        })
        # 用户不存在，应返回 401，而不是 403
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_refresh_requires_csrf(self, raw_client):
        """P0-S4: /auth/refresh 不再豁免 CSRF，必须携带正确的 X-CSRF-Token。"""
        await _register_and_login(raw_client)
        csrf_token = raw_client.cookies.get("csrf_token")
        assert csrf_token

        # 攻击者无法读取 cookie，因此无法构造正确的 X-CSRF-Token header；
        # 但浏览器仍会自动携带 csrf_token cookie。
        resp = await raw_client.post("/api/v1/auth/refresh")
        assert resp.status_code == 403
        assert resp.json()["message"] == "CSRF token missing or invalid"

        # 携带正确的 CSRF token 后应能刷新（即使 access token 已过期也不影响）
        resp = await raw_client.post(
            "/api/v1/auth/refresh",
            json={},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
