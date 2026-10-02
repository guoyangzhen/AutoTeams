"""Brand defaults and deliberate compatibility with existing installations."""
from pathlib import Path

PRODUCT_NAME = "AutoTeams"
PRODUCT_SLUG = "autoteams"
# Coordination and replay state must remain shared during a rolling upgrade.
LEGACY_STATE_NAMESPACE = "autofde"
LEGACY_AGENT_KEY_PREFIX = "afd_sk_"


def default_sqlite_url(directory: Path | None = None) -> str:
    """Use the new filename for fresh installs without hiding an existing database.

    The function does not rename/copy an active SQLite database or its WAL files.
    Two databases require an explicit URL instead of silently choosing one.
    """
    directory = directory or Path.cwd()
    current = directory / "autoteams.db"
    previous = directory / f"{LEGACY_STATE_NAMESPACE}.db"
    if current.exists() and previous.exists():
        raise RuntimeError(
            "检测到新旧两份 SQLite 数据库。请显式配置 DATABASE_URL 选择已有数据库，"
            "并设置 USE_SQLITE=false 使用该 URL；不要直接删除或合并业务数据。"
        )
    name = previous.name if previous.exists() else current.name
    return f"sqlite+aiosqlite:///./{name}"
