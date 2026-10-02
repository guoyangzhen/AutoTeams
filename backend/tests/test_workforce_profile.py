"""AutoTeams 4.0 数字员工档案与凭据加密单元测试。"""
import pytest
import uuid
from app.utils.credential_crypto import (
    CredentialDecryptError,
    encrypt_credential,
    decrypt_credential,
)
from app.models.workforce import WorkforceProfile
from app.schemas.workforce_profile import (
    WorkforceProfileCreate,
    WorkforceProfileUpdate,
    DutyBoundaries,
)
from app.services.workforce.profile_service import (
    generate_next_badge,
    create_workforce_profile,
    get_workforce_profile,
    list_workforce_profiles,
    update_workforce_profile,
    delete_workforce_profile,
    check_duty_boundary,
)


def test_credential_crypto_basic():
    """测试凭据加密与解密。"""
    raw_secret = "sk-wechat-enterprise-bot-token-2026-secret"
    encrypted = encrypt_credential(raw_secret)
    assert encrypted != raw_secret
    assert len(encrypted) > 20

    decrypted = decrypt_credential(encrypted)
    assert decrypted == raw_secret

    # 空值测试
    assert encrypt_credential("") == ""
    assert decrypt_credential("") == ""

    # P2-11 严格模式：历史明文/无效密文不再回退返回原文，而是抛出 CredentialDecryptError，
    # 防止数据库泄露时明文凭证被直接采用。
    with pytest.raises(CredentialDecryptError):
        decrypt_credential("plain-secret")


def test_check_duty_boundary():
    """测试岗位边界守则防线拦截。"""
    profile = WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id="ent-1",
        employee_badge="AT-2026-TECH-001",
        display_name="苏策",
        job_title="售前解决方案架构师",
        duty_boundaries={
            "allowed": ["产品报价咨询", "架构方案设计", "技术答疑"],
            "forbidden": ["直接修改线上数据库", "转账支付", "越权访问员工薪酬"],
        },
    )

    # 1. 正常业务动作匹配 allowed
    res_normal = check_duty_boundary(profile, "向客户提供产品报价咨询")
    assert res_normal.allowed is True
    assert "匹配岗位职责授权项" in res_normal.reason

    # 2. 违禁动作触发 forbidden 拦截
    res_forbidden = check_duty_boundary(profile, "尝试执行转账支付给外部供应商")
    assert res_forbidden.allowed is False
    assert "触发岗位禁止越权边界项" in res_forbidden.reason
    assert res_forbidden.matched_boundary == "转账支付"

    # 3. 未在白名单中的未授权动作
    res_unknown = check_duty_boundary(profile, "协助人事部门发布招聘信息")
    assert res_unknown.allowed is False
    assert "未在岗位明确授权的允许清单中" in res_unknown.reason


@pytest.mark.asyncio
async def test_workforce_profile_crud(db_session):
    """测试 WorkforceProfile 数据库 CRUD 操作。"""
    enterprise_id = f"test-ent-{uuid.uuid4().hex[:6]}"

    # 1. 验证工号自动生成格式
    badge = await generate_next_badge(db_session, enterprise_id, "TECH")
    assert badge.startswith("ATE-")
    assert "-TECH-" in badge

    # 2. 创建数字员工
    payload = WorkforceProfileCreate(
        display_name="测试专员-小策",
        job_title="客户支持工程师",
        department="TECH",
        duty_boundaries=DutyBoundaries(
            allowed=["解答客户疑问", "收集需求反馈"],
            forbidden=["修改系统配置"],
        ),
        tone_style="concise",
        authorized_flows=["flow-customer-qa"],
        accessible_knowledge_buckets=["kb-faq"],
        authorized_tools=["tool-search"],
        employment_status="shadow",
        performance_score=98.5,
    )

    profile = await create_workforce_profile(db_session, enterprise_id, payload)
    assert profile.id is not None
    assert profile.employee_badge == badge
    assert profile.display_name == "测试专员-小策"

    # 3. 读取详情
    fetched = await get_workforce_profile(db_session, profile.id)
    assert fetched is not None
    assert fetched.job_title == "客户支持工程师"

    # 4. 列表查询
    profiles = await list_workforce_profiles(db_session, enterprise_id)
    assert len(profiles) >= 1
    assert any(p.id == profile.id for p in profiles)

    # 5. 更新档案
    updated = await update_workforce_profile(
        db_session,
        profile.id,
        WorkforceProfileUpdate(display_name="资深专员-小策", performance_score=99.0),
    )
    assert updated is not None
    assert updated.display_name == "资深专员-小策"
    assert updated.performance_score == 99.0

    # 6. 删除档案
    deleted = await delete_workforce_profile(db_session, profile.id)
    assert deleted is True
    assert await get_workforce_profile(db_session, profile.id) is None
