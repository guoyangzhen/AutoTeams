"""向后兼容 shim：身份认证工具已迁移到 app.utils.auth 包（3.3.4）。

历史遗留：本文件曾承担 4 类职责（JWT token / CSRF / 密码 / 依赖注入），
现按职责拆分为 app.utils.auth.tokens / csrf / password / deps 四个子模块。

为避免破坏 22 个现有调用方，本文件保留为 re-export shim，所有公开符号
通过 __all__ 显式导出。新代码推荐直接从 app.utils.auth.<module> 导入。

迁移指引：
- from app.utils.security import create_access_token
+ from app.utils.auth.tokens import create_access_token
- from app.utils.security import get_current_user
+ from app.utils.auth.deps import get_current_user
"""
from app.utils.auth.tokens import (
    compute_request_fingerprint,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_token_max_age,
    hash_token,
    verify_token_hash,
)
from app.utils.auth.csrf import (
    CSRF_PROTECTED_METHODS,
    CSRF_TOKEN_NAME,
    generate_csrf_token,
    validate_csrf_token,
)
from app.utils.auth.password import (
    get_password_hash,
    verify_password,
)
from app.utils.auth.deps import (
    get_current_user,
    security_scheme,
)

__all__ = [
    # tokens
    "compute_request_fingerprint",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_token_max_age",
    "hash_token",
    "verify_token_hash",
    # csrf
    "CSRF_PROTECTED_METHODS",
    "CSRF_TOKEN_NAME",
    "generate_csrf_token",
    "validate_csrf_token",
    # password
    "get_password_hash",
    "verify_password",
    # deps
    "get_current_user",
    "security_scheme",
]
