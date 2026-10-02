"""CSRF 防护工具（Double Submit Cookie 模式）。

3.3.4: 从 utils/security.py 拆分而来，专注"跨站请求伪造防护"职责。

向后兼容：utils/security.py 仍作为 re-export shim 保留所有公开符号。
"""
import hmac
import secrets

from fastapi import Request

from app.config import settings


# BE-SEC-01: CSRF Token cookie 名称
CSRF_TOKEN_NAME = "csrf_token"
# BE-SEC-01: 需要 CSRF 防护的 HTTP 方法（状态变更操作）
CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def generate_csrf_token() -> str:
    """生成 Double Submit Cookie 用的 CSRF token。

    使用 secrets.token_urlsafe 生成高熵随机串，无需服务端持久化。
    """
    return secrets.token_urlsafe(32)


def validate_csrf_token(request: Request) -> bool:
    """校验请求携带的 CSRF token 是否与 cookie 中的值一致。

    规则：
    - 非状态变更方法（GET/HEAD/OPTIONS）直接通过
    - 未携带 access_token cookie 的请求（未登录）跳过校验，
      因为登录/注册等首条请求本身无法预先获得 csrf cookie
    - 已登录的状态变更请求必须同时提供 cookie 中的 csrf_token
      和 header X-CSRF-Token，且两者相等
    - P2-T10: 增加 token 熵检查，防止空值或弱值
    """
    if request.method not in CSRF_PROTECTED_METHODS:
        return True

    access_token = request.cookies.get(settings.COOKIE_ACCESS_TOKEN_NAME)
    if not access_token:
        # 未登录状态不强制 CSRF（依赖 SameSite=Lax 做基础防护）
        return True

    cookie_token = request.cookies.get(CSRF_TOKEN_NAME)
    header_token = request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token:
        return False

    # P2-T10: token 熵检查（secrets.token_urlsafe(32) 生成的 token 至少 43 字符）
    # 防止空值、预设的弱值、或被截断的 token
    if len(cookie_token) < 32 or len(header_token) < 32:
        return False

    return hmac.compare_digest(cookie_token, header_token)
