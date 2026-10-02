"""密码哈希工具（bcrypt）。

3.3.4: 从 utils/security.py 拆分而来，专注"密码存储与校验"职责。

向后兼容：utils/security.py 仍作为 re-export shim 保留所有公开符号。
"""
import bcrypt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode('utf-8')[:72]
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def get_password_hash(password: str) -> str:
    password_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')
