"""生成 AutoTeams 生产环境所需的各类随机密钥。

用法::

    python scripts/generate_secrets.py            # 打印全部密钥（可直接粘进 .env）
    python scripts/generate_secrets.py --only jwt # 只生成指定密钥
    python scripts/generate_secrets.py --output secrets.generated.env  # 写入文件（勿提交！）

密钥清单与后端 `backend/app/config.py` 中的字段一一对应：
- JWT_SECRET_KEY          会话 JWT 签名（>=32 字符）
- ENCRYPTION_KEY          模型 API 密钥等敏感数据的 Fernet 静态加密（>=48 字符）
- AUDIT_SIGNING_KEY       审计链 HMAC 防篡改签名
- AGENT_API_KEY_HMAC_SECRET 机器凭证 API key 的 HMAC 摘要密钥
- BRIDGE_INTERNAL_SECRET  Backend ↔ collaboration-service 内部鉴权

注意：
- 输出内容属于高敏感信息，请立即转移到安全的密钥管理系统或 .env（该文件
  已被 .gitignore 忽略），绝不要提交到仓库。
- 轮换 JWT_SECRET_KEY 会使所有已登录会话失效；轮换 ENCRYPTION_KEY 会导致
  已加密的模型 API 密钥无法解密（需重新录入），操作前请评估影响。
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

# (env 变量名, 说明, 字节长度)
SECRET_SPECS: list[tuple[str, str, int]] = [
    ("JWT_SECRET_KEY", "会话 JWT 签名密钥（轮换会使全部登录态失效）", 48),
    ("ENCRYPTION_KEY", "敏感数据 Fernet 加密密钥（轮换需重新录入已存密钥）", 48),
    ("AUDIT_SIGNING_KEY", "审计链 HMAC 签名密钥（轮换后旧链验证需旧钥）", 32),
    ("AGENT_API_KEY_HMAC_SECRET", "机器凭证 HMAC 摘要密钥（轮换后旧凭证全部失效）", 32),
    ("BRIDGE_INTERNAL_SECRET", "Backend 与协作服务内部鉴权密钥（需两端同步）", 32),
]


def generate(value_bytes: int) -> str:
    return secrets.token_urlsafe(value_bytes)


def generate_all(only: list[str] | None = None) -> list[tuple[str, str, str]]:
    """返回 [(变量名, 说明, 值)]；only 非空时只生成匹配项。"""
    results: list[tuple[str, str, str]] = []
    for name, desc, size in SECRET_SPECS:
        if only and name not in only:
            continue
        results.append((name, desc, generate(size)))
    unknown = set(only or []) - {spec[0] for spec in SECRET_SPECS}
    if unknown:
        raise SystemExit(f"未知密钥名称: {', '.join(sorted(unknown))}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 AutoTeams 生产密钥")
    parser.add_argument(
        "--only",
        nargs="*",
        default=[],
        help="只生成指定密钥（可选值: " + ", ".join(s[0] for s in SECRET_SPECS) + "）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="追加写入指定 .env 文件（默认仅打印到 stdout）",
    )
    args = parser.parse_args()

    entries = generate_all(args.only or None)
    if not entries:
        parser.print_help()
        return 1

    lines = [
        f"{name}={value}"
        for name, _desc, value in entries
    ]
    block = "\n".join(lines)

    if args.output:
        with args.output.open("a", encoding="utf-8") as fh:
            fh.write(block + "\n")
        print(f"已追加写入 {args.output}（共 {len(entries)} 项）。请勿将该文件提交到仓库！")
    else:
        print("# AutoTeams 生产密钥（生成于本地，请立即转移到安全位置）")
        for name, desc, _ in entries:
            print(f"# {name}: {desc}")
        print(block)

    return 0


if __name__ == "__main__":
    sys.exit(main())
