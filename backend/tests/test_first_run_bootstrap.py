"""首管理员 bootstrap 与配置一致性测试（AUD-12）。

覆盖审计 §AUD-12 中"全新机器按文档启动后走不通"的断点：

1. 生产关闭公开注册、演示 seed 被禁用后，没有任何可用方式创建第一个企业管理员；
2. `database.py` 只读 `os.getenv`，看不见项目根 `.env`，导致 `.env` 里配好的
   PostgreSQL 被静默替换成 SQLite —— 配置与实际连接的数据库不一致。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.enterprise import Enterprise
from app.models.user import User
from scripts.bootstrap_admin import BootstrapError, bootstrap
from app.utils.auth.password import verify_password

BACKEND_DIR = Path(__file__).resolve().parents[1]
STRONG_PASSWORD = "Str0ng-Bootstrap-Passphrase!"


@pytest.mark.asyncio
async def test_bootstrap_creates_enterprise_admin_and_audit(test_engine, monkeypatch):
    """一次性 bootstrap：企业 + 管理员 + 可审计记录。"""
    import scripts.bootstrap_admin as module

    # bootstrap 使用真实的 async_session_factory，这里把它指向测试库
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(module, "async_session_factory", factory)

    result = await bootstrap(
        email="Admin@Example.com",
        enterprise_name="首启企业",
        admin_name="管理员",
        password=STRONG_PASSWORD,
    )

    async with factory() as session:
        admin = (
            await session.execute(
                select(User).where(User.email == "admin@example.com")
            )
        ).scalar_one()
        assert admin.role == "admin"
        assert admin.enterprise_id == result["enterprise_id"]
        assert admin.is_active is True
        # 密码只存哈希
        assert admin.password_hash != STRONG_PASSWORD
        assert verify_password(STRONG_PASSWORD, admin.password_hash)

        enterprise = await session.get(Enterprise, result["enterprise_id"])
        assert enterprise.name == "首启企业"

        audits = (await session.execute(select(AuditLog))).scalars().all()
        assert any(
            entry.action == "bootstrap" and entry.resource_type == "enterprise"
            for entry in audits
        ), "bootstrap 必须留下可审计记录"


@pytest.mark.asyncio
async def test_bootstrap_is_one_time(test_engine, monkeypatch):
    """已有企业管理员时拒绝二次 bootstrap。"""
    import scripts.bootstrap_admin as module

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(module, "async_session_factory", factory)

    await bootstrap(
        email="first@example.com",
        enterprise_name="企业一",
        admin_name="管理员一",
        password=STRONG_PASSWORD,
    )
    with pytest.raises(BootstrapError) as excinfo:
        await bootstrap(
            email="second@example.com",
            enterprise_name="企业二",
            admin_name="管理员二",
            password=STRONG_PASSWORD,
        )
    assert "拒绝重复 bootstrap" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bootstrap_rejects_duplicate_email(test_engine, monkeypatch):
    import scripts.bootstrap_admin as module

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(module, "async_session_factory", factory)

    await bootstrap(
        email="dup@example.com",
        enterprise_name="企业",
        admin_name="管理员",
        password=STRONG_PASSWORD,
    )
    with pytest.raises(BootstrapError) as excinfo:
        await bootstrap(
            email="dup@example.com",
            enterprise_name="另一企业",
            admin_name="另一管理员",
            password=STRONG_PASSWORD,
        )
    assert "已存在用户" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bootstrap_rejects_weak_password(test_engine, monkeypatch):
    import scripts.bootstrap_admin as module

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(module, "async_session_factory", factory)

    for weak in ("short", "alllowercasepassword", "ALLUPPERCASEPASSWORD"):
        with pytest.raises(BootstrapError):
            await bootstrap(
                email=f"weak-{weak}@example.com",
                enterprise_name="弱口令企业",
                admin_name="管理员",
                password=weak,
            )


@pytest.mark.asyncio
async def test_bootstrap_rejects_invalid_email(test_engine, monkeypatch):
    import scripts.bootstrap_admin as module

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(module, "async_session_factory", factory)

    with pytest.raises(BootstrapError) as excinfo:
        await bootstrap(
            email="not-an-email",
            enterprise_name="企业",
            admin_name="管理员",
            password=STRONG_PASSWORD,
        )
    assert "邮箱格式非法" in str(excinfo.value)


def _probe_env(**overrides: str) -> dict:
    """构造用于子进程探测的环境：继承当前环境但清掉测试期的数据库变量。"""
    env = dict(os.environ)
    for key in ("USE_SQLITE", "DATABASE_URL"):
        env.pop(key, None)
    env["DEBUG"] = "true"
    env["PYTHONIOENCODING"] = "utf-8"
    env["JWT_SECRET_KEY"] = "test-secret-key-for-unit-tests-only-32chars-plus"
    env.update(overrides)
    return env


def test_database_url_precedence_env_over_default():
    """AUD-12：DATABASE_URL 环境变量必须被真正使用，而不是静默回落到 SQLite。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.database import DATABASE_URL; print(DATABASE_URL)",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_probe_env(DATABASE_URL="postgresql+asyncpg://probe:probe@127.0.0.1:5432/probe"),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "postgresql+asyncpg://probe" in completed.stdout


def test_database_url_use_sqlite_flag_overrides():
    """USE_SQLITE=true 是本地开发的显式选择，优先级最高。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.database import DATABASE_URL; print(DATABASE_URL)",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_probe_env(
            USE_SQLITE="true",
            DATABASE_URL="postgresql+asyncpg://probe:probe@127.0.0.1:5432/probe",
        ),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "sqlite" in completed.stdout
