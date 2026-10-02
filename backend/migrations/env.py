"""Alembic 迁移环境配置。

从 app.database 读取 DATABASE_URL，从 app.models 读取所有模型元数据，
确保迁移脚本与实际运行时配置一致。
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 将 backend 目录加入 sys.path，确保能 import app
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 导入 Base 和所有模型（确保 metadata 注册完整）
from app.database import Base, DATABASE_URL  # noqa: E402
from app import models  # noqa: E402,F401  导入所有模型以注册到 metadata

# Alembic 配置对象
config = context.config

# Alembic 使用同步引擎，需要将异步 URL 转为同步 URL
# sqlite+aiosqlite -> sqlite, postgresql+asyncpg -> postgresql+psycopg2
sync_url = DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata（Alembic 据此生成迁移）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # SQLite 不支持 ALTER，需要 batch 模式
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
