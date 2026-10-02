"""对话式 Agent 创建 Copilot 测试。

覆盖：
1. CopilotService.parse_user_intent - LLM tool calling 解析 + 正则兜底
2. CopilotService.auto_generate_plan - 基于文件特征生成 name/description/system_prompt
3. CopilotService.create_agent_from_message - 端到端流程，验证 build_agent_via_graph 被以 require_approval=False 调用
4. POST /setup/copilot - 成功路径、鉴权、参数校验、无尾斜杠无 307、错误映射
5. GET /folders/list-children - 目录浏览

mock 策略：
- llm_service 是模块级全局导入（from app.services.llm_service import llm_service），
  故 patch app.services.copilot_service.llm_service；ModelTier 仍为真实枚举，无需 mock
- scan_folder 在 copilot_service 顶部导入，patch app.services.copilot_service.scan_folder
- build_agent_via_graph 在函数内延迟导入，patch app.services.agent_graph.build_agent_via_graph
- async_session_factory 在函数内延迟导入（from app.database import），patch app.database.async_session_factory
"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.enterprise import Enterprise
from app.models.user import User
from app.services.copilot_service import CopilotService
from app.services.folder_scanner import ScannedFile


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后重置速率限制器，避免测试间互相影响。"""
    from app.utils.rate_limit import limiter
    limiter._storage.reset()
    yield
    limiter._storage.reset()


@pytest_asyncio.fixture
async def auth_client(client, test_engine):
    """注册用户并绑定企业（/setup/copilot 要求当前用户已绑定企业）。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        ent = Enterprise(id="ent-copilot-1", name="Copilot 测试企业")
        s.add(ent)
        await s.commit()

    await client.post("/api/v1/auth/register", json={
        "email": "test_copilot@test.com",
        "name": "测试",
        "password": "pass1234",
    })

    # register 创建的用户无企业归属，手动绑定企业 ID
    async with factory() as s:
        result = await s.execute(select(User).where(User.email == "test_copilot@test.com"))
        user = result.scalar_one()
        user.enterprise_id = "ent-copilot-1"
        await s.commit()

    return client


def _make_files() -> list[ScannedFile]:
    """构造扫描结果，用于 mock scan_folder。"""
    return [
        ScannedFile(name="hr_policy.pdf", path="/data/hr/hr_policy.pdf",
                    size=102400, file_type="pdf", extension=".pdf"),
        ScannedFile(name="faq.md", path="/data/hr/faq.md",
                    size=8192, file_type="markdown", extension=".md"),
        ScannedFile(name="onboarding.docx", path="/data/hr/onboarding.docx",
                    size=51200, file_type="word", extension=".docx"),
    ]


@contextmanager
def _mock_llm(reply: str):
    """统一 mock 模块级 llm_service.chat 返回 reply；reply=None 表示 LLM 抛异常。"""
    with patch("app.services.copilot_service.llm_service") as mock_llm:
        if reply is None:
            mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        else:
            mock_llm.chat = AsyncMock(return_value=reply)
        yield mock_llm


# ---------------------------------------------------------------------------
# CopilotService 单元测试
# ---------------------------------------------------------------------------

class TestParseUserIntent:
    """意图解析：LLM tool calling + 正则兜底。"""

    @pytest.mark.asyncio
    async def test_llm_extracts_folder_path(self):
        """LLM 返回合法 JSON 时应正确提取 folder_path。"""
        svc = CopilotService()
        llm_reply = '{"folder_path": "D:\\\\公司文档", "agent_name_hint": "HR 答疑助手", "description_hint": "新员工答疑"}'
        with _mock_llm(llm_reply):
            intent = await svc.parse_user_intent("用 D:\\公司文档 做一个 HR 答疑助手")

        assert intent["folder_path"] == "D:\\公司文档"
        assert intent["agent_name_hint"] == "HR 答疑助手"
        assert intent["description_hint"] == "新员工答疑"

    @pytest.mark.asyncio
    async def test_regex_fallback_when_llm_fails(self):
        """LLM 抛异常时应走正则兜底，从原文提取 Windows 盘符路径。"""
        svc = CopilotService()
        with _mock_llm(None):
            intent = await svc.parse_user_intent(
                "用 D:\\AIProjects\\AutoTeams\\backend\\sample_data 做一个技术问答助手"
            )

        assert "D:\\AIProjects\\AutoTeams\\backend\\sample_data" in intent["folder_path"]
        assert intent["folder_path"].startswith("D:")

    @pytest.mark.asyncio
    async def test_regex_fallback_unix_path(self):
        """正则兜底应支持 Unix 绝对路径。"""
        svc = CopilotService()
        with _mock_llm(None):
            intent = await svc.parse_user_intent("用 /home/user/docs 做助手")

        assert intent["folder_path"].startswith("/home/user/docs")

    @pytest.mark.asyncio
    async def test_no_path_returns_empty(self):
        """消息中无路径时应返回空 folder_path。"""
        svc = CopilotService()
        with _mock_llm("抱歉，我没理解"):
            intent = await svc.parse_user_intent("你好，今天天气怎么样")

        assert intent["folder_path"] == ""


class TestAutoGeneratePlan:
    """方案生成：基于文件特征生成对齐 AgentBuildState 的字段。"""

    @pytest.mark.asyncio
    async def test_plan_fields_align_with_build_state(self):
        """plan 输出应包含 name/description/system_prompt/folder_path 四个字段。"""
        svc = CopilotService()
        plan = await svc.auto_generate_plan("/data/hr", _make_files(), {
            "agent_name_hint": "HR 答疑助手",
            "description_hint": "",
        })

        assert set(plan.keys()) == {"name", "description", "system_prompt", "folder_path"}
        assert plan["name"] == "HR 答疑助手"
        assert plan["folder_path"] == "/data/hr"
        assert plan["system_prompt"]

    @pytest.mark.asyncio
    async def test_plan_name_falls_back_to_folder_name(self):
        """无 agent_name_hint 时，名称应为「文件夹名 + 助手」。"""
        svc = CopilotService()
        plan = await svc.auto_generate_plan("/data/hr", _make_files(), {
            "agent_name_hint": "",
            "description_hint": "",
        })

        assert plan["name"] == "hr助手"

    @pytest.mark.asyncio
    async def test_plan_description_uses_hint_when_provided(self):
        """有 description_hint 时应优先使用 hint 作为描述。"""
        svc = CopilotService()
        plan = await svc.auto_generate_plan("/data/hr", _make_files(), {
            "agent_name_hint": "",
            "description_hint": "这是一个自定义描述",
        })

        assert plan["description"] == "这是一个自定义描述"


class TestCreateAgentFromMessage:
    """端到端流程：解析 → 扫描 → 方案 → 构建。"""

    @pytest.mark.asyncio
    async def test_build_called_with_require_approval_false(self, test_engine):
        """核心断言：build_agent_via_graph 必须以 require_approval=False 调用。"""
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            ent = Enterprise(id="ent-build-1", name="构建测试企业")
            s.add(ent)
            user = User(id="u-build", email="build@test.com", name="构建",
                        password_hash="x", enterprise_id="ent-build-1")
            s.add(user)
            await s.commit()

        svc = CopilotService()
        llm_reply = '{"folder_path": "/data/hr", "agent_name_hint": "HR 答疑助手", "description_hint": ""}'

        with _mock_llm(llm_reply), \
                patch("app.services.copilot_service.scan_folder", return_value=_make_files()) as mock_scan, \
                patch("app.database.async_session_factory", factory), \
                patch("app.services.agent_graph.build_agent_via_graph",
                      new_callable=AsyncMock) as mock_build:
            mock_build.return_value = {
                "agent_id": "agent-001",
                "status": "completed",
                "thread_id": "thread-001",
            }

            async with factory() as db:
                result = await svc.create_agent_from_message(
                    "用 /data/hr 做一个 HR 答疑助手", user, db
                )

        # 验证 scan_folder 被调用（递归=True）
        mock_scan.assert_called_once()
        assert mock_scan.call_args[0][1] is True  # recursive=True

        # 核心断言：require_approval=False
        mock_build.assert_awaited_once()
        _, kwargs = mock_build.call_args
        assert kwargs.get("require_approval") is False
        assert kwargs.get("enterprise_id") == "ent-build-1"
        assert kwargs.get("name") == "HR 答疑助手"
        assert kwargs.get("folder_path") == "/data/hr"
        assert kwargs.get("thread_id")

        # 验证返回结构
        assert result["status"] == "building"
        assert result["thread_id"]
        assert result["agent_id"] == "agent-001"
        assert result["agent_name"] == "HR 答疑助手"
        assert result["redirect_url"] == f"/canvas/{result['thread_id']}"

    @pytest.mark.asyncio
    async def test_raises_when_no_enterprise(self, test_engine):
        """未绑定企业应抛 ValueError。"""
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = User(id="u-noent", email="noent@test.com", name="无企业",
                        password_hash="x")

        svc = CopilotService()
        with _mock_llm('{"folder_path": "/data/hr"}'):
            async with factory() as db:
                with pytest.raises(ValueError):
                    await svc.create_agent_from_message(
                        "用 /data/hr 做助手", user, db
                    )

    @pytest.mark.asyncio
    async def test_raises_when_path_not_parsed(self, auth_client, test_engine):
        """无法解析出路径时应抛 ValueError。"""
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = (await s.execute(
                select(User).where(User.email == "test_copilot@test.com")
            )).scalar_one()

        svc = CopilotService()
        with _mock_llm('{"folder_path": ""}'):
            async with factory() as db:
                with pytest.raises(ValueError):
                    await svc.create_agent_from_message(
                        "你好，今天天气怎么样", user, db
                    )

    @pytest.mark.asyncio
    async def test_raises_when_folder_not_found(self, auth_client, test_engine):
        """文件夹不存在应抛 FileNotFoundError。"""
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = (await s.execute(
                select(User).where(User.email == "test_copilot@test.com")
            )).scalar_one()

        svc = CopilotService()
        with _mock_llm('{"folder_path": "/data/missing"}'), \
                patch("app.services.copilot_service.scan_folder",
                      side_effect=FileNotFoundError("not found")):
            async with factory() as db:
                with pytest.raises(FileNotFoundError):
                    await svc.create_agent_from_message(
                        "用 /data/missing 做助手", user, db
                    )


# ---------------------------------------------------------------------------
# API 集成测试
# ---------------------------------------------------------------------------

class TestCopilotApi:
    """POST /setup/copilot 端点测试。"""

    @pytest.mark.asyncio
    async def test_copilot_success_no_trailing_slash_no_307(self, auth_client):
        """成功路径：无尾斜杠请求应返回 200（非 307）。"""
        with patch("app.services.copilot_service.scan_folder", return_value=_make_files()), \
                patch("app.database.async_session_factory"), \
                patch("app.services.agent_graph.build_agent_via_graph",
                      new_callable=AsyncMock) as mock_build:
            mock_build.return_value = {
                "agent_id": "agent-api-1",
                "status": "completed",
                "thread_id": "thread-api-1",
            }
            resp = await auth_client.post("/api/v1/setup/copilot", json={
                "message": "用 D:\\data\\hr 做一个 HR 答疑助手",
            })

        assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text}"
        assert resp.status_code != 307  # 显式断言无 307 重定向
        data = resp.json()["data"]
        assert data["status"] == "building"
        assert data["thread_id"]
        assert data["agent_name"]
        assert data["redirect_url"] == f"/canvas/{data['thread_id']}"

        # 验证 build 被以 require_approval=False 调用
        mock_build.assert_awaited_once()
        _, kwargs = mock_build.call_args
        assert kwargs.get("require_approval") is False

    @pytest.mark.asyncio
    async def test_copilot_route_registered_no_307(self, auth_client):
        """端点 /setup/copilot（无尾斜杠）应被直接路由，不触发 307。"""
        with patch("app.services.copilot_service.scan_folder", return_value=_make_files()), \
                patch("app.database.async_session_factory"), \
                patch("app.services.agent_graph.build_agent_via_graph",
                      new_callable=AsyncMock,
                      return_value={"agent_id": "x", "status": "completed"}):
            resp = await auth_client.post("/api/v1/setup/copilot", json={
                "message": "无路径消息",
            })
        # 路由已注册：不应是 307/404，应是业务 400（未解析出路径）
        assert resp.status_code != 307
        assert resp.status_code != 404
        assert resp.status_code in (200, 400)

    @pytest.mark.asyncio
    async def test_copilot_unauthorized(self, client):
        """未认证请求应返回 401/403。"""
        resp = await client.post("/api/v1/setup/copilot", json={
            "message": "用 D:\\data\\hr 做助手",
        })
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_copilot_no_enterprise_returns_403(self, client):
        """已登录但未绑定企业应返回 403。"""
        await client.post("/api/v1/auth/register", json={
            "email": "noent_copilot@test.com",
            "name": "无企业用户",
            "password": "pass1234",
        })
        resp = await client.post("/api/v1/setup/copilot", json={
            "message": "用 D:\\data\\hr 做助手",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_copilot_empty_message_returns_400(self, auth_client):
        """空消息应返回 400。"""
        resp = await auth_client.post("/api/v1/setup/copilot", json={
            "message": "   ",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_copilot_missing_message_returns_422(self, auth_client):
        """缺少 message 字段应返回 422。"""
        resp = await auth_client.post("/api/v1/setup/copilot", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_copilot_folder_not_found_returns_404(self, auth_client):
        """文件夹不存在应映射为 404。"""
        with patch("app.services.copilot_service.scan_folder",
                   side_effect=FileNotFoundError("not found")):
            resp = await auth_client.post("/api/v1/setup/copilot", json={
                "message": "用 /data/missing 做助手",
            })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_copilot_path_invalid_returns_400(self, auth_client):
        """路径不合法（ValueError）应映射为 400。"""
        with patch("app.services.copilot_service.scan_folder",
                   side_effect=ValueError("路径不合法")):
            resp = await auth_client.post("/api/v1/setup/copilot", json={
                "message": "用 ../../../etc 做助手",
            })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# folders/list-children 测试
# ---------------------------------------------------------------------------

class TestListChildrenApi:
    """GET /folders/list-children 端点测试。"""

    @pytest.mark.asyncio
    async def test_list_children_default_root(self, auth_client, upload_root):
        """不传 path 应列出 UPLOAD_ROOT 下的子项。"""
        import os
        import tempfile

        with tempfile.TemporaryDirectory(dir=upload_root) as tmpdir:
            sub_dir = os.path.join(tmpdir, "sub_dir")
            os.makedirs(sub_dir, exist_ok=True)
            with open(os.path.join(tmpdir, "readme.md"), "w", encoding="utf-8") as f:
                f.write("hello")

            resp = await auth_client.get("/api/v1/folders/list-children")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert "children" in data
            names = [c["name"] for c in data["children"]]
            # 临时目录应出现在子项列表中
            assert os.path.basename(tmpdir) in names

    @pytest.mark.asyncio
    async def test_list_children_no_trailing_slash_no_307(self, auth_client, upload_root):
        """list-children 端点无尾斜杠应返回 200（非 307）。"""
        resp = await auth_client.get("/api/v1/folders/list-children")
        assert resp.status_code == 200
        assert resp.status_code != 307

    @pytest.mark.asyncio
    async def test_list_children_unauthorized(self, client):
        """未认证应返回 401/403。"""
        resp = await client.get("/api/v1/folders/list-children")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_list_children_path_traversal_rejected(self, auth_client):
        """路径遍历应被拒绝（400）。"""
        resp = await auth_client.get(
            "/api/v1/folders/list-children", params={"path": "../../../etc"}
        )
        assert resp.status_code == 400
