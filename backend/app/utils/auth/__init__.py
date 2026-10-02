"""auth 包：身份认证相关工具的模块化拆分（3.3.4）。

子模块：
- tokens: JWT access/refresh token 签发、解码、哈希校验、请求指纹
- csrf: Double Submit Cookie 模式的 CSRF 防护
- password: bcrypt 密码哈希
- deps: FastAPI 依赖注入（get_current_user）

向后兼容：app.utils.security 仍保留为 re-export shim，现有导入路径不受影响。
新代码推荐直接从子模块导入以获得更清晰的依赖边界。
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
