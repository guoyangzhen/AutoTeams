"""租户隔离回归测试（AUD-01 / AUD-03 / AUD-07 / AUD-30）。

用真实的 FastAPI 应用 + ASGITransport，只把 `get_current_user` 替换成
合成租户 A 的**普通成员**（保留真实路由与业务逻辑）。这验证的是授权缺失，
不是绕过登录认证。

覆盖的复现来自 `docs/TECHNICAL_AUDIT_VALIDATION_2026-09-28.md` §5.2–§5.5：
1. MCP `runner_read_file` 读后端临时目录文件 → 现在必须失败；
2. `GET /teams/{B的团队}/tasks` 读他人任务 → 必须 404；
3. `POST /teams/{无关团队}/tasks/{B的任务}/review` → 必须 404 且不产生变更；
4. `POST /evolution/sop/{B的规程}/evolve` → 必须 404 且版本不变；
5. `POST /evolution/workforce/{id}/graduate` 普通成员 + 零门槛 → 必须 403。
"""
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.enterprise import Enterprise
from app.models.flow_card import FlowCardModel
from app.models.team_matrix import MatrixTask, WorkgroupTeam
from app.models.user import User
from app.utils.security import get_current_user
from app.models.workforce import WorkforceProfile

ENTERPRISE_A = "audit-enterprise-A"
ENTERPRISE_B = "audit-enterprise-B"


def _member() -> SimpleNamespace:
    return SimpleNamespace(
        id="user-A",
        enterprise_id=ENTERPRISE_A,
        role="member",
        is_active=True,
    )


@pytest_asyncio.fixture
async def tenant_client(test_engine):
    """挂载两个合成租户，并把当前用户固定为租户 A 的普通成员。"""

    async def override_get_db():
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(Enterprise(id=ENTERPRISE_A, name="审计企业A"))
        session.add(Enterprise(id=ENTERPRISE_B, name="审计企业B"))

        team_b = WorkgroupTeam(
            id="team-B",
            enterprise_id=ENTERPRISE_B,
            name="企业B工作组",
            leader_profile_id="lead-B",
            member_profile_ids=["dev-B"],
        )
        session.add(team_b)
        task_b = MatrixTask(
            id="task-B",
            team_id=team_b.id,
            title="企业B的机密任务",
            description="不应被企业A读取或修改",
            status="in_progress",
        )
        session.add(task_b)
        session.add(
            FlowCardModel(
                id="flow-B",
                enterprise_id=ENTERPRISE_B,
                flow_id="flow-B",
                name="企业B规程",
                version="1.0.0",
                flow_data={"flow_id": "flow-B", "guardrails": {"high_risk_confirmation": False}},
            )
        )
        session.add(
            WorkforceProfile(
                id="profile-A",
                enterprise_id=ENTERPRISE_A,
                employee_badge="ATE-2026-A-001",
                display_name="企业A员工",
                job_title="客服",
                employment_status="shadow",
                performance_score=50.0,
            )
        )
        await session.commit()

    async def override_current_user():
        return _member()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mcp_read_file_cannot_read_backend_files(tenant_client, tmp_path):
    """AUD-01：后端不再对调用方给出的路径执行 open()。"""
    client, _factory = tenant_client
    marker = tmp_path / "server-only-marker.txt"
    marker.write_text("AUDIT_SYNTHETIC_SERVER_ONLY_MARKER", encoding="utf-8")

    resp = await client.post(
        "/api/v1/mcp/call",
        json={
            "server_id": "local_runner",
            "tool_name": "runner_read_file",
            "arguments": {"device_id": "dev-A", "relative_path": str(marker)},
        },
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["success"] is False, body
    assert "AUDIT_SYNTHETIC_SERVER_ONLY_MARKER" not in resp.text


@pytest.mark.asyncio
async def test_mcp_read_file_rejects_escaping_relative_paths(tenant_client):
    """AUD-01：绝对路径与 .. 逃逸都不允许。"""
    client, _factory = tenant_client
    for path in ("/etc/passwd", "../../secret", "..\\..\\secret"):
        resp = await client.post(
            "/api/v1/mcp/call",
            json={
                "server_id": "local_runner",
                "tool_name": "runner_read_file",
                "arguments": {"device_id": "dev-A", "relative_path": path},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["success"] is False, path


@pytest.mark.asyncio
async def test_team_tasks_are_tenant_scoped(tenant_client):
    """AUD-03：企业A读不到企业B的任务。"""
    client, _factory = tenant_client
    resp = await client.get("/api/v1/teams/team-B/tasks")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_review_cannot_mutate_task(tenant_client):
    """AUD-03：跨企业 + 错配团队的验收请求不得产生任何数据库变更。"""
    client, factory = tenant_client
    for team_id in ("unrelated-team", "team-B"):
        resp = await client.post(
            f"/api/v1/teams/{team_id}/tasks/task-B/review",
            json={"approved": True, "feedback": {"by": "attacker"}},
        )
        assert resp.status_code == 404, (team_id, resp.status_code)

    async with factory() as session:
        task = (await session.execute(select(MatrixTask).where(MatrixTask.id == "task-B"))).scalar_one()
        assert task.status == "in_progress"
        assert task.review_feedback is None


@pytest.mark.asyncio
async def test_tenant_a_cannot_use_own_team_url_for_foreign_task(tenant_client):
    """AUD-03：URL 里的团队必须与任务所属团队一致。"""
    client, factory = tenant_client
    async with factory() as session:
        session.add(
            WorkgroupTeam(
                id="team-A",
                enterprise_id=ENTERPRISE_A,
                name="企业A工作组",
                leader_profile_id="lead-A",
                member_profile_ids=["dev-A"],
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/teams/team-A/tasks/task-B/review",
        json={"approved": True},
    )
    assert resp.status_code == 404

    async with factory() as session:
        task = (await session.execute(select(MatrixTask).where(MatrixTask.id == "task-B"))).scalar_one()
        assert task.status == "in_progress"


@pytest.mark.asyncio
async def test_sop_evolution_is_tenant_scoped(tenant_client):
    """AUD-07：企业A不能进化企业B的规程。"""
    client, factory = tenant_client
    resp = await client.post(
        "/api/v1/evolution/sop/flow-B/evolve",
        json={"feedback_notes": ["支付扣费"]},
    )
    assert resp.status_code == 404

    async with factory() as session:
        flow = (
            await session.execute(select(FlowCardModel).where(FlowCardModel.id == "flow-B"))
        ).scalar_one()
        assert flow.version == "1.0.0"
        assert flow.flow_data["guardrails"]["high_risk_confirmation"] is False


@pytest.mark.asyncio
async def test_graduation_requires_admin(tenant_client):
    """AUD-30：普通成员不能触发影子转正。"""
    client, _factory = tenant_client
    resp = await client.post(
        "/api/v1/evolution/workforce/profile-A/graduate",
        json={},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_graduation_rejects_out_of_range_thresholds(tenant_client):
    """AUD-30：门槛参数有取值边界。"""
    client, _factory = tenant_client

    async def override_admin():
        return SimpleNamespace(
            id="admin-A", enterprise_id=ENTERPRISE_A, role="admin", is_active=True
        )

    app.dependency_overrides[get_current_user] = override_admin
    try:
        for payload in ({"min_samples": 0}, {"pass_rate_threshold": 0.0}, {"min_samples": -3}):
            resp = await client.post(
                "/api/v1/evolution/workforce/profile-A/graduate", json=payload
            )
            assert resp.status_code == 422, (payload, resp.status_code)
    finally:
        app.dependency_overrides[get_current_user] = lambda: _member()


@pytest.mark.asyncio
async def test_unbound_user_cannot_read_enterprise_resources(tenant_client):
    """未归属企业的普通成员（公开注册用户）看不到任何企业资源。"""
    client, _factory = tenant_client

    async def override_unbound():
        return SimpleNamespace(
            id="user-public", enterprise_id=None, role="member", is_active=True
        )

    app.dependency_overrides[get_current_user] = override_unbound
    try:
        assert (await client.get("/api/v1/teams/team-B/tasks")).status_code == 403
        assert (await client.get("/api/v1/teams")).status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = lambda: _member()


@pytest.mark.asyncio
async def test_orm_metadata_matches_migrated_schema_shape():
    """AUD-05 兜底：11 张业务表必须真实存在于 ORM 元数据。"""
    expected = {
        "channel_accounts",
        "channel_identities",
        "counterfactual_diffs",
        "flow_cards",
        "matrix_tasks",
        "shadow_evaluation_sessions",
        "shared_blackboard_entries",
        "strike_teams",
        "task_selection_bids",
        "workforce_profiles",
        "workgroup_teams",
    }
    assert expected.issubset(set(Base.metadata.tables))
    # 确保每张表都有主键
    for name in expected:
        assert Base.metadata.tables[name].primary_key.columns
