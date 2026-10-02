"""P2-4 文件监控与增量更新测试。

覆盖：
1. TestComputeFileHash: 哈希计算正确性
2. TestFilePagination: 分页参数、默认值、limit 上限
3. TestFilePreview: 文本文件预览、二进制文件拒绝、不存在文件 404
4. TestIncrementalUpdate: 新增文件、修改文件（hash 变化）、删除文件、无变化
5. TestFileWatcher: watchdog 不可用时的降级行为、启动/停止监控
"""
import hashlib
import os
import uuid
from contextlib import contextmanager
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.enterprise import Enterprise
from app.models.file import File
from app.models.user import User
from app.services.incremental_updater import compute_file_hash, incremental_update
from app.services.file_watcher import (
    file_watcher_service,
    FileWatcherService,
    WATCHDOG_AVAILABLE,
)
from app.utils.error_codes import ErrorCode


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    """P3-5: 将 UPLOAD_ROOT 指向临时目录，避免路径安全校验失败。"""
    monkeypatch.setattr(settings, "UPLOAD_ROOT", str(tmp_path))
    return tmp_path


# ============================================================
# 辅助工具
# ============================================================

@contextmanager
def mock_processing():
    """Mock _process_file 和 VectorStoreService，避免依赖外部服务。"""
    async def _mock_process(path, file_type):
        return ["mock chunk content"]

    mock_vs = MagicMock()
    mock_vs.add_documents = MagicMock()
    mock_vs.delete_by_metadata = AsyncMock(return_value=0)

    with patch("app.services.incremental_updater._process_file", side_effect=_mock_process), \
         patch("app.services.incremental_updater.VectorStoreService.create", new=AsyncMock(return_value=mock_vs)):
        yield mock_vs


async def _create_enterprise_user_agent(test_engine, prefix="test"):
    """创建企业、用户、Agent，返回 (agent_id, enterprise_id)。"""
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


async def _register_user(client, test_engine, ent_id, prefix="test"):
    """P3-5: 注册用户并加入指定企业；httpx 自动保存 Cookie，无需返回 headers。"""
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"
    resp = await client.post("/api/v1/auth/register", json={
        "email": email,
        "name": f"{prefix}用户",
        "password": "pass1234",
    })
    assert resp.status_code == 201, f"注册失败: {resp.text}"

    user_id = resp.json()["data"]["user"]["id"]

    # 注册端点不接收 enterprise_id，直接在 DB 中更新用户企业归属
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        user = await s.get(User, user_id)
        if user:
            user.enterprise_id = ent_id
            await s.commit()


async def _create_file_record(test_engine, agent_id, file_path, file_type, name, size=100):
    """创建 File 记录，返回 file_id。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        f = File(
            agent_id=agent_id,
            original_name=name,
            file_path=file_path,
            file_size=size,
            file_type=file_type,
            status="completed",
        )
        s.add(f)
        await s.commit()
        await s.refresh(f)
        return str(f.id)


# ============================================================
# 1. 文件哈希计算测试
# ============================================================

class TestComputeFileHash:
    """文件哈希计算测试。"""

    def test_hash_correctness(self, tmp_path):
        """验证 SHA256 哈希计算正确。"""
        content = b"Hello, World!"
        file_path = tmp_path / "test.txt"
        file_path.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        actual = compute_file_hash(str(file_path))
        assert actual == expected

    def test_hash_changes_with_content(self, tmp_path):
        """文件内容变化时哈希应不同。"""
        file_path = tmp_path / "test.txt"

        file_path.write_bytes(b"content A")
        hash_a = compute_file_hash(str(file_path))

        file_path.write_bytes(b"content B")
        hash_b = compute_file_hash(str(file_path))

        assert hash_a != hash_b

    def test_hash_consistent(self, tmp_path):
        """同一文件多次计算哈希应一致。"""
        file_path = tmp_path / "test.txt"
        file_path.write_bytes(b"consistent content")

        hash1 = compute_file_hash(str(file_path))
        hash2 = compute_file_hash(str(file_path))
        assert hash1 == hash2

    def test_hash_large_file(self, tmp_path):
        """大文件哈希计算应正确（分块读取）。"""
        content = b"x" * 100000  # 100KB
        file_path = tmp_path / "large.txt"
        file_path.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        actual = compute_file_hash(str(file_path))
        assert actual == expected


# ============================================================
# 2. 文件分页列表测试
# ============================================================

class TestFilePagination:
    """文件分页列表 API 测试。"""

    @pytest.mark.asyncio
    async def test_default_pagination(self, client, test_engine):
        """默认分页应返回所有文件（limit=50）。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "pag")
        for i in range(5):
            await _create_file_record(test_engine, agent_id, f"/tmp/file_{i}.txt", "document", f"file_{i}.txt")
        await _register_user(client, test_engine, ent_id, "pag")

        resp = await client.get("/api/v1/files")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 5
        assert len(data["files"]) == 5

    @pytest.mark.asyncio
    async def test_custom_limit(self, client, test_engine):
        """自定义 limit 应限制返回数量。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "lim")
        for i in range(5):
            await _create_file_record(test_engine, agent_id, f"/tmp/lim_{i}.txt", "document", f"lim_{i}.txt")
        await _register_user(client, test_engine, ent_id, "lim")

        resp = await client.get("/api/v1/files?limit=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 5
        assert len(data["files"]) == 2

    @pytest.mark.asyncio
    async def test_limit_exceeds_max(self, client, test_engine):
        """limit 超过 100 应返回 422 验证错误。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "max")
        await _create_file_record(test_engine, agent_id, "/tmp/max.txt", "document", "max.txt")
        await _register_user(client, test_engine, ent_id, "max")

        resp = await client.get("/api/v1/files?limit=200")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_offset(self, client, test_engine):
        """offset 应跳过前 N 条记录。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "off")
        for i in range(5):
            await _create_file_record(test_engine, agent_id, f"/tmp/off_{i}.txt", "document", f"off_{i}.txt")
        await _register_user(client, test_engine, ent_id, "off")

        resp = await client.get("/api/v1/files?limit=10&offset=3")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 5
        assert len(data["files"]) == 2  # 5 - 3 = 2

    @pytest.mark.asyncio
    async def test_filter_by_agent(self, client, test_engine):
        """按 agent_id 过滤应只返回该 agent 的文件。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "filt")
        await _create_file_record(test_engine, agent_id, "/tmp/filt.txt", "document", "filt.txt")
        # 创建另一个 agent 无文件
        other_agent_id, _ = await _create_enterprise_user_agent(test_engine, "filt2")
        await _register_user(client, test_engine, ent_id, "filt")

        resp = await client.get(f"/api/v1/files?agent_id={other_agent_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert len(data["files"]) == 0

    @pytest.mark.asyncio
    async def test_empty_list(self, client, test_engine):
        """空列表应返回 total=0 和空数组。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "empty")
        await _register_user(client, test_engine, ent_id, "empty")

        resp = await client.get("/api/v1/files")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert len(data["files"]) == 0


# ============================================================
# 3. 文件预览测试
# ============================================================

class TestFilePreview:
    """文件内容预览 API 测试。"""

    @pytest.mark.asyncio
    async def test_preview_text_file(self, client, test_engine, upload_root):
        """预览文本文件应返回内容。"""
        content = "这是测试文件内容，用于预览功能测试。" * 50
        file_path = upload_root / "preview.txt"
        file_path.write_text(content, encoding="utf-8")

        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "prev")
        file_id = await _create_file_record(
            test_engine, agent_id, str(file_path), "document", "preview.txt",
            size=len(content.encode("utf-8")),
        )
        await _register_user(client, test_engine, ent_id, "prev")

        resp = await client.get(f"/api/v1/files/{file_id}/preview?max_chars=50")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["file_id"] == file_id
        assert data["original_name"] == "preview.txt"
        assert data["file_type"] == "document"
        assert len(data["preview"]) <= 50
        assert data["truncated"] is True

    @pytest.mark.asyncio
    async def test_preview_full_file(self, client, test_engine, upload_root):
        """预览短文件应返回全部内容且 truncated=False。"""
        content = "短内容"
        file_path = upload_root / "short.txt"
        file_path.write_text(content, encoding="utf-8")

        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "short")
        file_id = await _create_file_record(
            test_engine, agent_id, str(file_path), "document", "short.txt",
            size=len(content.encode("utf-8")),
        )
        await _register_user(client, test_engine, ent_id, "short")

        resp = await client.get(f"/api/v1/files/{file_id}/preview")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["preview"] == content
        assert data["truncated"] is False

    @pytest.mark.asyncio
    async def test_preview_binary_file_rejected(self, client, test_engine):
        """二进制文件（image 类型）应返回 400。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "bin")
        file_id = await _create_file_record(
            test_engine, agent_id, "/tmp/test.jpg", "image", "test.jpg",
        )
        await _register_user(client, test_engine, ent_id, "bin")

        resp = await client.get(f"/api/v1/files/{file_id}/preview")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_video_file_rejected(self, client, test_engine):
        """视频文件应返回 400。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "vid")
        file_id = await _create_file_record(
            test_engine, agent_id, "/tmp/test.mp4", "video", "test.mp4",
        )
        await _register_user(client, test_engine, ent_id, "vid")

        resp = await client.get(f"/api/v1/files/{file_id}/preview")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_preview_nonexistent_file(self, client, test_engine):
        """不存在的文件 ID 应返回 404。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "nonexist")
        await _register_user(client, test_engine, ent_id, "nonexist")

        resp = await client.get("/api/v1/files/nonexistent-id/preview")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_file_not_on_disk(self, client, test_engine):
        """文件在磁盘上不存在应返回 404。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "nodisk")
        file_id = await _create_file_record(
            test_engine, agent_id, "/tmp/nonexistent_file.txt", "document", "nonexistent.txt",
        )
        await _register_user(client, test_engine, ent_id, "nodisk")

        resp = await client.get(f"/api/v1/files/{file_id}/preview")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_code_file(self, client, test_engine, upload_root):
        """代码文件（code 类型）应支持预览。"""
        content = "def hello():\n    print('Hello, World!')\n"
        file_path = upload_root / "hello.py"
        file_path.write_text(content, encoding="utf-8")

        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "code")
        file_id = await _create_file_record(
            test_engine, agent_id, str(file_path), "code", "hello.py",
            size=len(content.encode("utf-8")),
        )
        await _register_user(client, test_engine, ent_id, "code")

        resp = await client.get(f"/api/v1/files/{file_id}/preview")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "hello" in data["preview"]


# ============================================================
# 4. 增量更新测试
# ============================================================

class TestIncrementalUpdate:
    """增量更新服务测试。"""

    @pytest.mark.asyncio
    async def test_add_new_files(self, test_engine, upload_root):
        """新增文件：首次增量更新应添加所有文件。"""
        (upload_root / "file1.txt").write_text("文件1内容", encoding="utf-8")
        (upload_root / "file2.txt").write_text("文件2内容", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "add")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                stats = await incremental_update(db, agent_id, str(upload_root))

        assert stats["added"] == 2
        assert stats["updated"] == 0
        assert stats["deleted"] == 0
        assert stats["unchanged"] == 0

    @pytest.mark.asyncio
    async def test_no_changes(self, test_engine, upload_root):
        """无变化：第二次增量更新应全部 unchanged。"""
        (upload_root / "file1.txt").write_text("文件1内容", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "nochange")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        async with factory() as db:
            with mock_processing():
                stats = await incremental_update(db, agent_id, str(upload_root))

        assert stats["added"] == 0
        assert stats["updated"] == 0
        assert stats["deleted"] == 0
        assert stats["unchanged"] == 1

    @pytest.mark.asyncio
    async def test_modified_file(self, test_engine, upload_root):
        """修改文件：hash 变化应触发更新。"""
        file_path = upload_root / "file1.txt"
        file_path.write_text("原始内容", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "mod")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        # 修改文件内容
        file_path.write_text("修改后的内容", encoding="utf-8")

        async with factory() as db:
            with mock_processing():
                stats = await incremental_update(db, agent_id, str(upload_root))

        assert stats["updated"] == 1
        assert stats["added"] == 0
        assert stats["deleted"] == 0
        assert stats["unchanged"] == 0

    @pytest.mark.asyncio
    async def test_deleted_file(self, test_engine, upload_root):
        """删除文件：磁盘上不存在的文件应被清理。"""
        file_path = upload_root / "file1.txt"
        file_path.write_text("文件1内容", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "del")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        # 从磁盘删除文件
        file_path.unlink()

        async with factory() as db:
            with mock_processing():
                stats = await incremental_update(db, agent_id, str(upload_root))

        assert stats["deleted"] == 1
        assert stats["added"] == 0
        assert stats["updated"] == 0
        assert stats["unchanged"] == 0

    @pytest.mark.asyncio
    async def test_mixed_changes(self, test_engine, upload_root):
        """混合场景：同时存在新增、修改、删除、无变化。"""
        # 初始文件
        f1 = upload_root / "keep.txt"
        f2 = upload_root / "modify.txt"
        f3 = upload_root / "delete.txt"
        f1.write_text("保持不变", encoding="utf-8")
        f2.write_text("原始内容", encoding="utf-8")
        f3.write_text("将被删除", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "mixed")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        # 修改 f2，删除 f3，新增 f4
        f2.write_text("修改后的内容", encoding="utf-8")
        f3.unlink()
        (upload_root / "new.txt").write_text("新文件", encoding="utf-8")

        async with factory() as db:
            with mock_processing():
                stats = await incremental_update(db, agent_id, str(upload_root))

        assert stats["added"] == 1      # new.txt
        assert stats["updated"] == 1    # modify.txt
        assert stats["deleted"] == 1    # delete.txt
        assert stats["unchanged"] == 1  # keep.txt

    @pytest.mark.asyncio
    async def test_file_record_has_content_hash(self, test_engine, upload_root):
        """增量更新后 File 记录应包含 content_hash。"""
        (upload_root / "file1.txt").write_text("测试内容", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "hash")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        async with factory() as db:
            result = await db.execute(select(File).where(File.agent_id == agent_id))
            files = result.scalars().all()
            assert len(files) == 1
            assert files[0].content_hash is not None
            assert len(files[0].content_hash) == 64  # SHA256 hex 长度

    @pytest.mark.asyncio
    async def test_deleted_file_record_removed(self, test_engine, upload_root):
        """删除文件后 File 记录应从数据库移除。"""
        file_path = upload_root / "file1.txt"
        file_path.write_text("文件1内容", encoding="utf-8")

        agent_id, _ = await _create_enterprise_user_agent(test_engine, "rm")

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        file_path.unlink()

        async with factory() as db:
            with mock_processing():
                await incremental_update(db, agent_id, str(upload_root))

        async with factory() as db:
            result = await db.execute(select(File).where(File.agent_id == agent_id))
            files = result.scalars().all()
            assert len(files) == 0


# ============================================================
# 5. 文件监控服务测试
# ============================================================

class TestFileWatcher:
    """文件监控服务测试。"""

    def test_is_available_returns_bool(self):
        """is_available 应返回布尔值。"""
        result = file_watcher_service.is_available()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_graceful_degradation_when_unavailable(self, tmp_path):
        """watchdog 不可用时应优雅降级。"""
        service = FileWatcherService()
        with patch("app.services.file_watcher.WATCHDOG_AVAILABLE", False):
            assert service.is_available() is False
            result = await service.start_watching("test-agent-degraded", str(tmp_path))
            assert result is False

    @pytest.mark.asyncio
    async def test_start_watching_nonexistent_folder(self):
        """监控不存在的文件夹应返回 False。"""
        if not WATCHDOG_AVAILABLE:
            pytest.skip("watchdog 未安装")

        service = FileWatcherService()
        agent_id = f"watch-nonexist-{uuid.uuid4().hex[:8]}"
        result = await service.start_watching(agent_id, "/nonexistent/path/12345")
        assert result is False

    @pytest.mark.asyncio
    async def test_start_and_stop_watching(self, upload_root):
        """启动和停止监控。"""
        if not WATCHDOG_AVAILABLE:
            pytest.skip("watchdog 未安装")

        service = FileWatcherService()
        agent_id = f"watch-start-{uuid.uuid4().hex[:8]}"

        started = await service.start_watching(agent_id, str(upload_root))
        assert started is True

        await service.stop_watching(agent_id)
        # 重复停止不应报错
        await service.stop_watching(agent_id)

    @pytest.mark.asyncio
    async def test_stop_all(self, upload_root):
        """停止所有监控。"""
        if not WATCHDOG_AVAILABLE:
            pytest.skip("watchdog 未安装")

        service = FileWatcherService()
        agent_id = f"watch-all-{uuid.uuid4().hex[:8]}"

        await service.start_watching(agent_id, str(upload_root))
        await service.stop_all()
        # 重复调用不应报错
        await service.stop_all()

    @pytest.mark.asyncio
    async def test_stop_nonexistent_watching(self):
        """停止不存在的监控不应报错。"""
        service = FileWatcherService()
        await service.stop_watching("nonexistent-agent-12345")
        # 不应抛出异常

    @pytest.mark.asyncio
    async def test_restart_watching(self, upload_root):
        """重新启动监控应先停止旧的。"""
        if not WATCHDOG_AVAILABLE:
            pytest.skip("watchdog 未安装")

        service = FileWatcherService()
        agent_id = f"watch-restart-{uuid.uuid4().hex[:8]}"

        await service.start_watching(agent_id, str(upload_root))
        # 重新启动不应报错
        started = await service.start_watching(agent_id, str(upload_root))
        assert started is True

        await service.stop_watching(agent_id)


# ============================================================
# 6. 文件上传测试（BE-SEC-07）
# ============================================================

class TestFileUpload:
    """真正的文件上传端点测试。"""

    @pytest.mark.asyncio
    async def test_upload_text_file_success(self, client, test_engine, upload_root):
        """上传合法文本文件应成功并创建 File 记录。"""
        content = "这是上传的测试文件内容".encode("utf-8")
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "upload")
        await _register_user(client, test_engine, ent_id, "upload")

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.txt", content, "text/plain")},
        )
        assert resp.status_code == 201, f"上传失败: {resp.text}"
        data = resp.json()["data"]
        assert data["agent_id"] == agent_id
        assert data["original_name"] == "test.txt"
        assert data["file_type"] == "document"
        assert data["file_size"] == len(content)
        assert data["status"] == "uploaded"
        # 物理文件应写入 upload_root/<user_id>/（响应中的 file_path 已脱敏为相对 upload_root 的路径）
        assert os.path.exists(os.path.join(upload_root, data["file_path"]))

    @pytest.mark.asyncio
    async def test_upload_requires_enterprise(self, client, test_engine, upload_root):
        """未绑定企业的用户不能上传。"""
        content = b"content"
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "noent")
        await _register_user(client, test_engine, ent_id, "noent")

        # 将用户 enterprise_id 置空
        from sqlalchemy.ext.asyncio import async_sessionmaker
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            result = await s.execute(select(User).where(User.email.like("noent-%")))
            user = result.scalar_one()
            user.enterprise_id = None
            await s.commit()

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.txt", content, "text/plain")},
        )
        assert resp.status_code == 403, f"unexpected response: {resp.status_code} {resp.text}"
        assert resp.json().get("message") == ErrorCode.ENTERPRISE_ACCESS_DENIED, f"unexpected body: {resp.text}"

    @pytest.mark.asyncio
    async def test_upload_agent_not_found(self, client, test_engine, upload_root):
        """上传到一个不存在的 agent 应返回 404。"""
        content = b"content"
        _, ent_id = await _create_enterprise_user_agent(test_engine, "noagent")
        await _register_user(client, test_engine, ent_id, "noagent")

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": "nonexistent-agent-id"},
            files={"file": ("test.txt", content, "text/plain")},
        )
        assert resp.status_code == 404
        assert resp.json()["message"] == ErrorCode.AGENT_NOT_FOUND

    @pytest.mark.asyncio
    async def test_upload_agent_other_enterprise_forbidden(self, client, test_engine, upload_root):
        """不能上传文件到其他企业的 agent。"""
        content = b"content"
        # 创建两个企业
        agent_id_1, ent_id_1 = await _create_enterprise_user_agent(test_engine, "ent1")
        agent_id_2, ent_id_2 = await _create_enterprise_user_agent(test_engine, "ent2")
        # 用户归属 ent_id_1
        await _register_user(client, test_engine, ent_id_1, "cross")

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id_2},
            files={"file": ("test.txt", content, "text/plain")},
        )
        assert resp.status_code == 403
        assert resp.json()["message"] == ErrorCode.ENTERPRISE_ACCESS_DENIED

    @pytest.mark.asyncio
    async def test_upload_disallowed_extension(self, client, test_engine, upload_root):
        """不允许的扩展名应返回 FILE_TYPE_NOT_ALLOWED。"""
        content = b"content"
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "badext")
        await _register_user(client, test_engine, ent_id, "badext")

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.exe", content, "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == ErrorCode.FILE_TYPE_NOT_ALLOWED

    @pytest.mark.asyncio
    async def test_upload_wrong_content_type(self, client, test_engine, upload_root):
        """扩展名与 MIME 类型明显不匹配应拒绝。"""
        content = b"content"
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "badmime")
        await _register_user(client, test_engine, ent_id, "badmime")

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.txt", content, "image/png")},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == ErrorCode.FILE_TYPE_NOT_ALLOWED

    @pytest.mark.asyncio
    async def test_upload_wrong_magic_bytes(self, client, test_engine, upload_root):
        """PNG 文件 Magic Bytes 不匹配应拒绝。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "badmagic")
        await _register_user(client, test_engine, ent_id, "badmagic")

        # 内容不是 PNG，但扩展名是 png
        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.png", b"not a png", "image/png")},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == ErrorCode.FILE_TYPE_NOT_ALLOWED

    @pytest.mark.asyncio
    async def test_upload_empty_file(self, client, test_engine, upload_root):
        """空文件应返回 FILE_EMPTY。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "empty")
        await _register_user(client, test_engine, ent_id, "empty")

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.txt", b"", "text/plain")},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == ErrorCode.FILE_EMPTY

    @pytest.mark.asyncio
    async def test_upload_oversized_file(self, client, test_engine, upload_root, monkeypatch):
        """超过大小上限的文件应返回 FILE_SIZE_EXCEEDED。"""
        agent_id, ent_id = await _create_enterprise_user_agent(test_engine, "oversize")
        await _register_user(client, test_engine, ent_id, "oversize")

        # 将文档大小上限临时设为 5 字节
        from app.config import settings
        monkeypatch.setattr(settings, "MAX_FILE_SIZE_DOCUMENT", 5)

        resp = await client.post(
            "/api/v1/files/upload",
            data={"agent_id": agent_id},
            files={"file": ("test.txt", b"this is too large", "text/plain")},
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == ErrorCode.FILE_SIZE_EXCEEDED
