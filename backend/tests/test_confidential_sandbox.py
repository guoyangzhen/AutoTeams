"""P1-SANDBOX: 涉密文件沙箱隔离与授权测试。"""
import io

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.user import User


async def _create_enterprise_user_agent(test_engine, prefix="conf"):
    """创建企业、用户、Agent，返回 (agent_id, enterprise_id)。"""
    import uuid

    ent_id = f"ent-{prefix}-{uuid.uuid4().hex[:8]}"
    agent_id = f"agent-{prefix}-{uuid.uuid4().hex[:8]}"

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        ent = Enterprise(id=ent_id, name=f"{prefix}测试企业")
        s.add(ent)
        agent = Agent(id=agent_id, enterprise_id=ent_id, name=f"{prefix}Agent", status="ready")
        s.add(agent)
        await s.commit()

    return agent_id, ent_id


async def _register_user(client, test_engine, ent_id, prefix="conf", role="member"):
    """注册用户并加入指定企业。"""
    import uuid

    email = f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": f"{prefix}用户",
        "password": "pass1234",
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"

    user_id = resp.json()["data"]["user"]["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        user = await s.get(User, user_id)
        if user:
            user.enterprise_id = ent_id
            user.role = role
            await s.commit()

    return user_id


class TestConfidentialDetection:
    """涉密文件自动识别测试。"""

    @pytest.mark.asyncio
    async def test_confidential_file_is_isolated(self, client, test_engine, upload_root):
        """文件名含机密关键词时，应自动存入沙箱目录。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "iso")
        admin_id = await _register_user(client, test_engine, ent_id, "admin", role="admin")

        content = b"this is confidential content"
        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("机密文档.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 201, f"上传失败: {resp.text}"
        data = resp.json()["data"]
        assert data["is_confidential"] is True
        assert data["confidential_status"] == "auto_detected"
        # 路径应在 sandbox 下
        assert "sandbox" in data["file_path"]

    @pytest.mark.asyncio
    async def test_highly_confidential_requires_auth(self, client, test_engine, upload_root):
        """极度私密文件被正确标记且默认需要授权。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "high")
        admin_id = await _register_user(client, test_engine, ent_id, "highadmin", role="admin")

        content = b"top secret content"
        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("绝密文件.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 201
        file_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["is_highly_confidential"] is True
        assert resp.json()["data"]["confidential_status"] == "pending_authorization"


class TestBatchUpload:
    """P1-UPLOAD: 批量文件/文件夹上传测试。"""

    @pytest.mark.asyncio
    async def test_batch_upload_multiple_files(self, client, test_engine, upload_root):
        """批量上传多个文件，应返回成功/失败汇总。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "batch")
        await _register_user(client, test_engine, ent_id, "batchadmin", role="admin")

        resp = await client.post(
            "/api/v1/files/batch-upload",
            data={"agent_id": agent_id},
            files=[
                ("files", ("手册.txt", io.BytesIO(b"content one"), "text/plain")),
                ("files", ("报表.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")),
            ],
        )
        assert resp.status_code == 201, f"批量上传失败: {resp.text}"
        data = resp.json()["data"]
        assert data["total"] == 2
        assert data["successful"] == 2
        assert data["failed"] == 0
        assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_batch_upload_partial_failure(self, client, test_engine, upload_root):
        """批量上传时单个文件类型不合法，其他文件应成功。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "batchfail")
        await _register_user(client, test_engine, ent_id, "batchfailadmin", role="admin")

        resp = await client.post(
            "/api/v1/files/batch-upload",
            data={"agent_id": agent_id},
            files=[
                ("files", ("手册.txt", io.BytesIO(b"content one"), "text/plain")),
                ("files", ("恶意.exe", io.BytesIO(b"binary"), "application/octet-stream")),
            ],
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["total"] == 2
        assert data["successful"] == 1
        assert data["failed"] == 1
        failed_item = data["items"][1]
        assert failed_item["status"] == "error"
        assert failed_item["original_name"] == "恶意.exe"


class TestConfidentialAccessControl:
    """涉密文件访问控制测试。"""

    @pytest.mark.asyncio
    async def test_member_cannot_access_highly_confidential(self, client, test_engine, upload_root):
        """普通成员无法访问极度私密文件。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "deny")
        admin_id = await _register_user(client, test_engine, ent_id, "denyadmin", role="admin")

        # 注册成员并记录邮箱
        member_email = "denymember-test@test.com"
        resp = await client.post("/api/v1/auth/register", json={
            "email": member_email,
            "name": "拒绝成员",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        member_id = resp.json()["data"]["user"]["id"]
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = await s.get(User, member_id)
            user.enterprise_id = ent_id
            user.role = "member"
            await s.commit()

        # 管理员上传极度私密文件
        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("绝密.txt", io.BytesIO(b"secret"), "text/plain")},
        )
        assert resp.status_code == 201
        file_id = resp.json()["data"]["id"]

        # 切换为成员登录
        resp = await client.post("/api/v1/auth/login", json={
            "email": member_email,
            "password": "pass1234",
        })
        assert resp.status_code == 200

        resp = await client.get(f"/api/v1/files/{file_id}")
        assert resp.status_code == 403
        assert resp.json()["message"] == "FILE_CONFIDENTIAL_ACCESS_DENIED"

    @pytest.mark.asyncio
    async def test_admin_can_grant_and_member_can_access(self, client, test_engine, upload_root):
        """管理员授权后，普通成员可访问极度私密文件。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "grant")

        # 注册管理员并记录邮箱（用于后续登录刷新 token role）
        admin_email = "grantadmin-test@test.com"
        resp = await client.post("/api/v1/auth/register", json={
            "email": admin_email,
            "name": "授权管理员",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        admin_id = resp.json()["data"]["user"]["id"]
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = await s.get(User, admin_id)
            user.enterprise_id = ent_id
            user.role = "admin"
            await s.commit()

        member_email = "grantmember-test@test.com"
        resp = await client.post("/api/v1/auth/register", json={
            "email": member_email,
            "name": "授权成员",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        member_id = resp.json()["data"]["user"]["id"]
        async with factory() as s:
            user = await s.get(User, member_id)
            user.enterprise_id = ent_id
            user.role = "member"
            await s.commit()

        # 管理员上传极度私密文件
        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("绝密.txt", io.BytesIO(b"secret"), "text/plain")},
        )
        assert resp.status_code == 201
        file_id = resp.json()["data"]["id"]

        # 重新登录管理员以刷新 token 中的 role 声明
        resp = await client.post("/api/v1/auth/login", json={
            "email": admin_email,
            "password": "pass1234",
        })
        assert resp.status_code == 200

        # 管理员授权成员
        resp = await client.post(
            f"/api/v1/files/{file_id}/confidential/grant",
            data={"target_user_id": member_id},
        )
        assert resp.status_code == 200, f"授权失败: {resp.text}"
        assert resp.json()["data"]["user_id"] == member_id

        # 切换为成员登录
        resp = await client.post("/api/v1/auth/login", json={
            "email": member_email,
            "password": "pass1234",
        })
        assert resp.status_code == 200

        resp = await client.get(f"/api/v1/files/{file_id}")
        assert resp.status_code == 200, f"成员访问失败: {resp.text}"
