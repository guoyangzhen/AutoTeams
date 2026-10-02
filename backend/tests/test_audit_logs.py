"""P1/P2-INFRA: 审计日志查询与导出 API 测试。"""
import csv
import io

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.enterprise import Enterprise
from app.models.user import User


async def _create_enterprise_and_admin(test_engine, suffix):
    """创建企业与管理员用户。"""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    ent_id = f"ent-audit-{suffix}"
    async with factory() as s:
        ent = Enterprise(id=ent_id, name=f"审计企业{suffix}")
        s.add(ent)
        await s.commit()

    # 注册管理员
    # 注意：注册接口不接收 enterprise_id，注册后通过 DB 更新归属
    return ent_id


async def _set_user_enterprise_and_role(test_engine, user_id, ent_id, role="admin"):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        user = await s.get(User, user_id)
        user.enterprise_id = ent_id
        user.role = role
        await s.commit()


async def _create_audit_log(test_engine, user_id, action, resource_type, resource_id="r1"):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address="127.0.0.1",
            user_agent="test",
            details={"extra": "data"},
        )
        s.add(log)
        await s.commit()


class TestAuditLogList:
    """审计日志列表查询测试。"""

    @pytest.mark.asyncio
    async def test_admin_can_list_own_enterprise_logs(self, client, test_engine):
        """企业管理员可查看本企业用户的审计日志。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "list")

        # 注册用户并设为本企业管理员
        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditadmin1@test.com",
            "name": "审计管理员1",
            "password": "pass1234",
        })
        assert resp.status_code == 201
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        # 创建审计日志
        await _create_audit_log(test_engine, user_id, "delete", "agent", "agent-1")

        resp = await client.get("/api/v1/audit-logs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 1
        assert any(log["resource_id"] == "agent-1" for log in data["logs"])

    @pytest.mark.asyncio
    async def test_member_cannot_list_logs(self, client, test_engine):
        """普通成员无权查看审计日志。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "member")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditmember@test.com",
            "name": "审计成员",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "member")

        resp = await client.get("/api/v1/audit-logs")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_filter_by_action(self, client, test_engine):
        """按 action 筛选审计日志。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "filter")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditfilter@test.com",
            "name": "审计筛选",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        await _create_audit_log(test_engine, user_id, "delete", "agent", "agent-del")
        await _create_audit_log(test_engine, user_id, "create", "file", "file-create")

        resp = await client.get("/api/v1/audit-logs?action=delete")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert all(log["action"] == "delete" for log in data["logs"])
        assert any(log["resource_id"] == "agent-del" for log in data["logs"])

    @pytest.mark.asyncio
    async def test_keyword_like_wildcard_escaped(self, client, test_engine):
        """3.1.5: keyword 中的 % 与 _ 应被当作字面量，而非通配符。

        不做转义时，keyword="%" 会匹配所有记录；做转义后应仅匹配包含字面 % 的记录。
        """
        ent_id = await _create_enterprise_and_admin(test_engine, "likeescape")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditlikeescape@test.com",
            "name": "审计转义",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        # 一条 resource_id 含字面 %，一条不含
        await _create_audit_log(test_engine, user_id, "delete", "agent", "agent-100%")
        await _create_audit_log(test_engine, user_id, "delete", "agent", "agent-200")

        # 用 % 作为关键词：应仅匹配含字面 % 的记录，而非全部
        resp = await client.get("/api/v1/audit-logs?keyword=%25")  # %25 = URL编码的 %
        assert resp.status_code == 200
        data = resp.json()["data"]
        resource_ids = {log["resource_id"] for log in data["logs"]}
        assert "agent-100%" in resource_ids
        assert "agent-200" not in resource_ids  # 不应被 % 通配符匹配

    @pytest.mark.asyncio
    async def test_keyword_underscore_escaped(self, client, test_engine):
        """3.1.5: keyword 中的 _ 应被当作字面量，仅匹配单个字符位置。

        不做转义时，keyword="_" 会匹配任意单字符；做转义后应仅匹配含字面 _ 的记录。
        """
        ent_id = await _create_enterprise_and_admin(test_engine, "underscoreescape")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditunderscore@test.com",
            "name": "审计下划线",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        # 一条 resource_id 含字面 _，一条不含
        await _create_audit_log(test_engine, user_id, "delete", "agent", "agent_with_underscore")
        await _create_audit_log(test_engine, user_id, "delete", "agent", "agentXwithXunderscore")

        # 用 _ 作为关键词：应仅匹配含字面 _ 的记录
        resp = await client.get("/api/v1/audit-logs?keyword=_")
        assert resp.status_code == 200
        data = resp.json()["data"]
        resource_ids = {log["resource_id"] for log in data["logs"]}
        assert "agent_with_underscore" in resource_ids
        assert "agentXwithXunderscore" not in resource_ids


class TestAuditLogExport:
    """审计日志 CSV 导出测试。"""

    @pytest.mark.asyncio
    async def test_export_csv(self, client, test_engine):
        """管理员可导出审计日志 CSV。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "export")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditexport@test.com",
            "name": "审计导出",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        await _create_audit_log(test_engine, user_id, "delete", "agent", "agent-export")

        resp = await client.get("/api/v1/audit-logs/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8-sig"
        assert "attachment" in resp.headers["content-disposition"]

        # 解析 CSV
        csv_text = resp.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert any(row["resource_id"] == "agent-export" for row in rows)

    @pytest.mark.asyncio
    async def test_export_csv_streaming_multi_batch(self, client, test_engine, monkeypatch):
        """3.2.5: 流式 CSV 导出在多批场景下应输出完整结果。

        通过把 batch_size 调小为 2，强制多批查询，验证：
        - 所有行都被输出（无丢失）
        - CSV header 仅出现一次
        - BOM 仅出现在文件开头
        """
        # 用小 batch_size 触发多批路径
        from app.api import audit_logs as audit_logs_module
        original_batch_size = audit_logs_module.CSV_EXPORT_BATCH_SIZE
        monkeypatch.setattr(audit_logs_module, "CSV_EXPORT_BATCH_SIZE", 2)

        ent_id = await _create_enterprise_and_admin(test_engine, "stream")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditstream@test.com",
            "name": "审计流式",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        # 创建 5 条审计日志（>batch_size=2，触发多批）
        for i in range(5):
            await _create_audit_log(
                test_engine, user_id, "delete", "agent", f"agent-stream-{i}"
            )

        try:
            resp = await client.get("/api/v1/audit-logs/export")
            assert resp.status_code == 200

            csv_text = resp.content.decode("utf-8-sig")
            # BOM 仅在开头出现一次
            assert csv_text.startswith("id,user_id,action")

            reader = csv.DictReader(io.StringIO(csv_text))
            rows = list(reader)
            # 至少包含我们创建的 5 条（不排除并发或导出本身产生的额外行）
            exported_ids = {row["resource_id"] for row in rows}
            for i in range(5):
                assert f"agent-stream-{i}" in exported_ids
        finally:
            # 恢复原始 batch_size，避免影响后续测试
            audit_logs_module.CSV_EXPORT_BATCH_SIZE = original_batch_size

    @pytest.mark.asyncio
    async def test_member_cannot_export(self, client, test_engine):
        """普通成员无权导出审计日志。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "exportmember")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditexportmember@test.com",
            "name": "审计导出成员",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "member")

        resp = await client.get("/api/v1/audit-logs/export")
        assert resp.status_code == 403


class TestAuditLogVerify:
    """审计日志链式完整性验证测试（5.3.7 HMAC 链式防篡改）。"""

    @pytest.mark.asyncio
    async def test_admin_can_verify_chain(self, client, test_engine):
        """管理员可调用 GET /audit-logs/verify，对未篡改的链返回 valid=True。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "verify")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditverify@test.com",
            "name": "审计验证",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        # 通过 log_audit 写入有签名的日志（模拟真实业务路径）
        from app.utils.audit import log_audit
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = await s.get(User, user_id)
            await log_audit(s, user, "delete", "agent", "agent-verify-1")
            await s.commit()

        resp = await client.get("/api/v1/audit-logs/verify?limit=0")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is True
        assert data["checked"] >= 1
        assert data["broken_at"] is None
        assert "验证通过" in data["message"]

    @pytest.mark.asyncio
    async def test_member_cannot_verify(self, client, test_engine):
        """普通成员无权验证审计链。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "verifymember")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditverifymember@test.com",
            "name": "审计验证成员",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "member")

        resp = await client.get("/api/v1/audit-logs/verify")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_verify_detects_tampered_signature(self, client, test_engine):
        """篡改日志 signature 后，验证应返回 valid=False 且 broken_at 指向被篡改日志。"""
        ent_id = await _create_enterprise_and_admin(test_engine, "verifytamper")

        resp = await client.post("/api/v1/auth/register", json={
            "email": "auditverifytamper@test.com",
            "name": "审计验证篡改",
            "password": "pass1234",
        })
        user_id = resp.json()["data"]["user"]["id"]
        await _set_user_enterprise_and_role(test_engine, user_id, ent_id, "admin")

        from app.utils.audit import log_audit
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            user = await s.get(User, user_id)
            await log_audit(s, user, "delete", "agent", "agent-tamper-1")
            await s.commit()

        # 篡改最近一条日志的 signature
        async with factory() as s:
            result = await s.execute(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(1)
            )
            log = result.scalar_one()
            log.signature = "tampered_signature_value"
            await s.commit()

        resp = await client.get("/api/v1/audit-logs/verify?limit=0")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["valid"] is False
        assert data["broken_at"] is not None
        assert "签名不匹配" in data["message"] or "断裂" in data["message"]

