"""文件夹扫描 API 测试。

覆盖：
1. POST /folders/scan - 扫描文件夹
2. 路径校验（缺失/不存在/路径遍历）
3. 权限校验
"""
import os
import tempfile

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()





class TestFoldersApi:
    """文件夹扫描 API 测试。"""

    @pytest.mark.asyncio
    async def test_scan_folder_missing_path(self, authenticated_client):
        """缺少 path 字段应返回 422。"""
        resp = await authenticated_client.post("/api/v1/folders/scan", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_scan_nonexistent_folder(self, authenticated_client):
        """扫描不存在的路径应返回错误（路径不在允许根目录下，返回 400）。"""
        resp = await authenticated_client.post("/api/v1/folders/scan", json={
            "path": "/nonexistent_folder_xyz_12345",
        })
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_scan_unauthorized(self, client):
        """未认证扫描请求应返回 401/403。"""
        resp = await client.post("/api/v1/folders/scan", json={
            "path": "./test_data",
        })
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_scan_invalid_path(self, authenticated_client):
        """路径遍历攻击应被拒绝（400）。"""
        resp = await authenticated_client.post("/api/v1/folders/scan", json={
            "path": "../../../etc/passwd",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_scan_valid_folder(self, authenticated_client):
        """扫描合法文件夹应返回文件列表。"""
        from app.config import settings

        os.makedirs(settings.UPLOAD_ROOT, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=settings.UPLOAD_ROOT) as tmpdir:
            # 创建测试文件
            test_file = os.path.join(tmpdir, "test_doc.txt")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("hello")

            resp = await authenticated_client.post("/api/v1/folders/scan", json={
                "path": tmpdir,
            })
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert "files" in data
            assert len(data["files"]) == 1
            assert data["files"][0]["name"] == "test_doc.txt"


class TestFoldersUpload:
    """POST /folders/upload 文件夹上传 API 测试。"""

    @pytest.mark.asyncio
    async def test_upload_success(self, authenticated_client):
        """上传合法文件夹（相对路径）应成功并返回可扫描的暂存目录。"""
        resp = await authenticated_client.post(
            "/api/v1/folders/upload",
            files=[
                ("files", ("company/01-organization.md", b"# org", "text/markdown")),
                ("files", ("company/02-process.md", b"# process", "text/markdown")),
            ],
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["file_count"] == 2
        assert data["total_size"] > 0
        assert data["folder_path"]
        # 返回的暂存目录应位于 UPLOAD_ROOT 内，可被 scan_folder 扫描（修复 FOLDER_PATH_INVALID）
        from app.services.folder_scanner import scan_folder
        scanned = scan_folder(data["folder_path"], recursive=True)
        assert len(scanned) == 2

    @pytest.mark.asyncio
    async def test_upload_path_traversal_rejected(self, authenticated_client):
        """上传包含路径遍历（..）的文件应返回 400。"""
        resp = await authenticated_client.post(
            "/api/v1/folders/upload",
            files=[("files", ("../evil.txt", b"x", "text/plain"))],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_empty_rejected(self, authenticated_client):
        """无文件上传（必填 files 缺失）应返回 422。"""
        resp = await authenticated_client.post("/api/v1/folders/upload")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_too_many_files_rejected(self, authenticated_client):
        """超过单次文件数量上限应返回 400。"""
        from app.api.folders import _FOLDER_UPLOAD_MAX_FILES
        files = [
            ("files", (f"f{i}.txt", b"x", "text/plain"))
            for i in range(_FOLDER_UPLOAD_MAX_FILES + 1)
        ]
        resp = await authenticated_client.post("/api/v1/folders/upload", files=files)
        assert resp.status_code == 400
