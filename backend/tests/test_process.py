"""文档处理任务 API 测试。

覆盖：
1. 参数校验与权限（未绑定企业、缺字段、未授权）
2. 不存在任务的 404 处理（get/cancel/report）
3. 空列表查询

不测试真实文档处理（需真实文件夹），聚焦于 API 端点的参数校验和权限边界。
"""
import asyncio
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.utils.error_codes import ErrorCode
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()





class TestProcessStartValidation:
    """处理任务启动的参数校验测试。"""

    @pytest.mark.asyncio
    async def test_start_processing_without_enterprise(self, authenticated_client):
        """未绑定企业的用户启动处理任务应返回 403。"""
        resp = await authenticated_client.post("/api/v1/process/start", json={
            "folder_path": "/tmp/test_docs",
            "enterprise_id": "ent-xxx",
            "agent_name": "测试Agent",
        })
        assert resp.status_code == 403
        assert resp.json()["message"] == ErrorCode.ENTERPRISE_ACCESS_DENIED

    @pytest.mark.asyncio
    async def test_start_processing_missing_fields(self, authenticated_client):
        """缺少必填字段应返回 422。"""
        # 只发 folder_path，缺 enterprise_id 和 agent_name
        resp = await authenticated_client.post("/api/v1/process/start", json={
            "folder_path": "/tmp/test_docs",
        })
        assert resp.status_code == 422


class TestProcessTaskNotFound:
    """不存在任务的 404 处理测试。"""

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, enterprise_authenticated_client):
        """GET /process/{task_id} 不存在的 task_id 应返回 404。"""
        resp = await enterprise_authenticated_client.get("/api/v1/process/nonexistent-task-id")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.TASK_NOT_FOUND

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, enterprise_authenticated_client):
        """POST /process/{task_id}/cancel 不存在的 task 应返回 404。"""
        resp = await enterprise_authenticated_client.post("/api/v1/process/nonexistent-task-id/cancel")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.TASK_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_task_report_nonexistent(self, enterprise_authenticated_client):
        """GET /process/{task_id}/report 不存在的 task 应返回 404。"""
        resp = await enterprise_authenticated_client.get("/api/v1/process/nonexistent-task-id/report")
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.TASK_NOT_FOUND


class TestProcessTaskCancel:
    """AUD-17：取消通过数据库标志投递，跨进程生效。"""

    @pytest.mark.asyncio
    async def test_cancel_pending_task_marks_cancelled(self, client, test_engine):
        """pending 任务被取消后立即进入终态。"""
        from app.models.enterprise import Enterprise
        from app.models.user import User

        ent_id = f"ent-cancel-{uuid.uuid4().hex[:8]}"
        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            s.add(Enterprise(id=ent_id, name="取消测试企业"))
            await s.commit()

        resp = await client.post("/api/v1/auth/register", json={
            "email": f"cancel-{uuid.uuid4().hex[:8]}@test.com",
            "name": "取消测试用户",
            "password": "pass1234",
        })
        assert resp.status_code == 201, f"注册失败: {resp.text}"
        user_id = resp.json()["data"]["user"]["id"]

        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            user = await s.get(User, user_id)
            user.enterprise_id = ent_id
            await s.commit()

        started = await client.post("/api/v1/process/start", json={
            "folder_path": "/tmp/test_cancel",
            "enterprise_id": ent_id,
            "agent_name": "取消测试Agent",
        })
        assert started.status_code == 200, f"启动任务失败: {started.text}"
        task_id = started.json()["data"]["task_id"]
        # 新的契约：入队后不启动任何协程，任务处于 pending
        assert started.json()["data"]["status"] == "pending"

        cancel_resp = await client.post(f"/api/v1/process/{task_id}/cancel")
        assert cancel_resp.status_code == 200, f"取消失败: {cancel_resp.text}"
        assert cancel_resp.json()["data"]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_sets_database_flag_for_running_task(self, client, test_engine):
        """processing 任务的取消写库标志，worker 观察后收尾（跨进程信号）。"""
        from datetime import timedelta

        from app.models.enterprise import Enterprise
        from app.models.processing_task import ProcessingTask
        from app.models.user import User
        from app.services.processing_queue import is_cancel_requested
        from app.utils.time import utcnow

        ent_id = f"ent-cancel2-{uuid.uuid4().hex[:8]}"
        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            s.add(Enterprise(id=ent_id, name="取消测试企业2"))
            await s.commit()

        resp = await client.post("/api/v1/auth/register", json={
            "email": f"cancel2-{uuid.uuid4().hex[:8]}@test.com",
            "name": "取消测试用户2",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        user_id = resp.json()["data"]["user"]["id"]

        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            user = await s.get(User, user_id)
            user.enterprise_id = ent_id
            await s.commit()

        started = await client.post("/api/v1/process/start", json={
            "folder_path": "/tmp/test_cancel_running",
            "enterprise_id": ent_id,
            "agent_name": "取消运行Agent",
        })
        assert started.status_code == 200, started.text
        task_id = started.json()["data"]["task_id"]

        # 模拟 worker 已领取（processing + 有效租约）
        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            task = await s.get(ProcessingTask, task_id)
            task.status = "processing"
            task.lease_owner = "worker-A"
            task.lease_until = utcnow() + timedelta(seconds=600)
            await s.commit()

        cancel_resp = await client.post(f"/api/v1/process/{task_id}/cancel")
        assert cancel_resp.status_code == 200, cancel_resp.text

        # 独立 session（模拟另一个进程）能读到取消信号
        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            assert await is_cancel_requested(s, task_id) is True

    @pytest.mark.asyncio
    async def test_duplicate_start_reuses_active_task(self, client, test_engine):
        """同一 (企业, 目录, Agent) 的重复提交复用既有任务（幂等）。"""
        from app.models.enterprise import Enterprise
        from app.models.user import User

        ent_id = f"ent-idem-{uuid.uuid4().hex[:8]}"
        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            s.add(Enterprise(id=ent_id, name="幂等测试企业"))
            await s.commit()

        resp = await client.post("/api/v1/auth/register", json={
            "email": f"idem-{uuid.uuid4().hex[:8]}@test.com",
            "name": "幂等用户",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        user_id = resp.json()["data"]["user"]["id"]
        async with async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )() as s:
            user = await s.get(User, user_id)
            user.enterprise_id = ent_id
            await s.commit()

        payload = {
            "folder_path": "/tmp/idem_docs",
            "enterprise_id": ent_id,
            "agent_name": "幂等Agent",
        }
        first = await client.post("/api/v1/process/start", json=payload)
        assert first.status_code == 200, first.text
        second = await client.post("/api/v1/process/start", json=payload)
        assert second.status_code == 200, second.text
        assert first.json()["data"]["task_id"] == second.json()["data"]["task_id"]


class TestProcessListAndAuth:
    """任务列表与权限校验测试。"""

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, authenticated_client):
        """空列表应返回空数组。"""
        resp = await authenticated_client.get("/api/v1/process")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["tasks"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_unauthorized_access(self, client):
        """未带 token 访问处理任务列表应返回 401/403。"""
        resp = await client.get("/api/v1/process")
        assert resp.status_code in (401, 403)
