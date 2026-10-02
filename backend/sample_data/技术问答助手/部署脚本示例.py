"""AutoTeams 一键部署脚本示例（Docker Compose 模式）。

运行前请确认：
  1. 已安装 Docker 20+ 与 Docker Compose v2+
  2. 已准备 .env.prod 文件（参考 .env.prod.example）
  3. PostgreSQL/Redis/ChromaDB 密码已强随机生成
"""
import subprocess
import sys
from pathlib import Path


def run(cmd: str, check: bool = True) -> int:
    """执行 shell 命令并实时打印输出。"""
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True)
    if check and result.returncode != 0:
        print(f"命令失败（退出码 {result.returncode}）", file=sys.stderr)
        sys.exit(result.returncode)
    return result.returncode


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    # 1. 检查 .env.prod
    env_file = repo_root / ".env.prod"
    if not env_file.exists():
        print("错误：未找到 .env.prod，请先复制 .env.prod.example 并填写凭证", file=sys.stderr)
        sys.exit(1)

    # 2. 拉取最新镜像
    run("docker compose -f docker-compose.prod.yml --env-file .env.prod pull")

    # 3. 启动服务（后端启动时会自动执行 alembic upgrade head + seed_demo + seed_vectors）
    run("docker compose -f docker-compose.prod.yml --env-file .env.prod up -d")

    # 4. 健康检查
    print("等待后端就绪...")
    run('curl -fsS http://localhost/health || exit 1', check=False)

    print("\n部署完成。访问 http://localhost 体验。")
    print("测试账号：demo@autoteams.example / demo123456")


if __name__ == "__main__":
    main()
