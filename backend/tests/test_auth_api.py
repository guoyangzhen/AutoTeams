"""认证 API 集成测试：注册、登录、获取当前用户。"""
import uuid

import pytest


class TestRegister:
    """用户注册测试。"""

    @pytest.mark.asyncio
    async def test_register_success(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "email": "newuser@test.com",
            "name": "新用户",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["user"]["email"] == "newuser@test.com"
        assert data["user"]["name"] == "新用户"
        # P1-1 + BE-SEC-01: token 已迁移到 HttpOnly Cookie，响应体中不再返回 token
        assert "token" not in data
        set_cookie = resp.headers.get("set-cookie", "")
        assert "access_token" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "csrf_token" in set_cookie
        assert "password" not in data["user"]

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client):
        payload = {
            "email": "dup@test.com",
            "name": "用户1",
            "password": "pass1234",
        }
        await client.post("/api/v1/auth/register", json=payload)
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_missing_fields(self, client):
        resp = await client.post("/api/v1/auth/register", json={
            "email": "missing@test.com",
        })
        assert resp.status_code == 422


class TestLogin:
    """用户登录测试。"""

    @pytest.mark.asyncio
    async def test_login_success(self, client):
        await client.post("/api/v1/auth/register", json={
            "email": "login@test.com",
            "name": "登录用户",
            "password": "pass1234",
        })
        resp = await client.post("/api/v1/auth/login", json={
            "email": "login@test.com",
            "password": "pass1234",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["user"]["email"] == "login@test.com"
        # P1-1 + BE-SEC-01: token 已迁移到 HttpOnly Cookie，同时下发 csrf_token
        assert "token" not in data
        set_cookie = resp.headers.get("set-cookie", "")
        assert "access_token" in set_cookie
        assert "csrf_token" in set_cookie

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        await client.post("/api/v1/auth/register", json={
            "email": "wrongpw@test.com",
            "name": "用户",
            "password": "correctpass",
        })
        resp = await client.post("/api/v1/auth/login", json={
            "email": "wrongpw@test.com",
            "password": "wrongpass",
        })
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client):
        resp = await client.post("/api/v1/auth/login", json={
            "email": "nobody@test.com",
            "password": "anypass",
        })
        assert resp.status_code == 401


class TestAuthMe:
    """/auth/me 端点测试。"""

    @pytest.mark.asyncio
    async def test_get_me_with_valid_token(self, client):
        email = f"me-{uuid.uuid4()}@test.com"
        resp = await client.post("/api/v1/auth/register", json={
            "email": email,
            "name": "我的用户",
            "password": "pass1234",
        })
        # P1-1: httpx client 会自动保存 Set-Cookie 并在后续请求中携带
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["data"]["email"] == email

    @pytest.mark.asyncio
    async def test_get_me_without_token(self, client):
        resp = await client.get("/api/v1/auth/me")
        # HTTPBearer 默认返回 403 当没有提供凭据
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_me_with_invalid_token(self, client):
        # P1-1: token 已从 HttpOnly Cookie 读取，不再通过 Authorization 头传递
        client.cookies.clear()
        client.cookies.set("access_token", "invalid.token.here")
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401


class TestAuthUpdateMe:
    """A1：PUT /auth/me 端点测试。"""

    @pytest.mark.asyncio
    async def test_update_me_success(self, client):
        email = f"update-{uuid.uuid4()}@test.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "name": "旧名字",
            "password": "pass1234",
        })
        resp = await client.put("/api/v1/auth/me", json={"name": "新名字"})
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "新名字"

    @pytest.mark.asyncio
    async def test_update_me_blank_name_rejected(self, client):
        email = f"updateblank-{uuid.uuid4()}@test.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "name": "旧名字",
            "password": "pass1234",
        })
        resp = await client.put("/api/v1/auth/me", json={"name": "   "})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_me_requires_auth(self, client):
        resp = await client.put("/api/v1/auth/me", json={"name": "x"})
        assert resp.status_code in (401, 403)


class TestAuthChangePassword:
    """A1：POST /auth/change-password 端点测试。"""

    @pytest.mark.asyncio
    async def test_change_password_success(self, client):
        email = f"chpass-{uuid.uuid4()}@test.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "name": "改密",
            "password": "pass1234",
        })
        resp = await client.post("/api/v1/auth/change-password", json={
            "current_password": "pass1234",
            "new_password": "newpass12345",
        })
        assert resp.status_code == 200
        # 用新密码应能登录
        await client.post("/api/v1/auth/logout")
        login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "newpass12345",
        })
        assert login.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, client):
        email = f"chpassbad-{uuid.uuid4()}@test.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "name": "改密",
            "password": "pass1234",
        })
        resp = await client.post("/api/v1/auth/change-password", json={
            "current_password": "wrongpass",
            "new_password": "newpass12345",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_change_password_too_short(self, client):
        email = f"chpassshort-{uuid.uuid4()}@test.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "name": "改密",
            "password": "pass1234",
        })
        resp = await client.post("/api/v1/auth/change-password", json={
            "current_password": "pass1234",
            "new_password": "short",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_change_password_requires_auth(self, client):
        resp = await client.post("/api/v1/auth/change-password", json={
            "current_password": "pass1234",
            "new_password": "newpass12345",
        })
        assert resp.status_code in (401, 403)
