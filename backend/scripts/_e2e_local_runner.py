"""端到端测试：协作工作台与本地工具桥接（Local Runner）。

驱动真实服务（后端 8000 + 协作服务 3001 + 本地 Runner），验证：
1. 注册用户并绑定企业（DB 直写，模拟企业用户）
2. 登录获取 Cookie + CSRF
3. 注册本地路径授权 → 生成 setup 命令
4. 启动本地 Runner（Node 进程），携带 setup 命令外连 /bridge
5. 等待 Runner 上报 connected
6. 通过 /run 驱动 list/read/write/cli 任务
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid

import httpx

BACKEND = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
COLLAB = os.environ.get("COLLAB_URL", "http://127.0.0.1:3001")
LOCAL_PATH = os.path.abspath(os.environ.get("E2E_LOCAL_PATH", "./_e2e_local"))
RUNNER_DIR = os.path.abspath(os.environ.get("RUNNER_DIR", "./local-runner"))

EMAIL = f"e2e_{uuid.uuid4().hex[:8]}@test.com"
PASSWORD = "e2ePass123!"
ENTERPRISE_ID = f"e2e-ent-{uuid.uuid4().hex[:8]}"

results = []


def section(name):
    print(f"\n{'='*70}\n### {name}\n{'='*70}")


async def create_enterprise_and_user():
    """在 DB 中直接创建企业 + 用户（模拟企业成员），并绑定 enterprise_id。"""
    sys.path.insert(0, os.path.abspath("./backend"))
    # 对齐运行中的后端：使用 backend/autoteams.db（避免相对 cwd 产生新的空库）
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.abspath(
        os.path.join("backend", "autoteams.db")
    ).replace("\\", "/")
    import asyncio
    from sqlalchemy import select

    from app.database import get_db
    from app.models.enterprise import Enterprise
    from app.models.user import User
    from app.utils.auth.password import get_password_hash as hash_password

    async for session in get_db():
        ent = Enterprise(id=ENTERPRISE_ID, name="E2E 测试企业")
        session.add(ent)
        await session.commit()
        user = User(
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            name="E2E 用户",
            role="member",
            enterprise_id=ENTERPRISE_ID,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        # 记录 user id 供后续审计断言
        return user.id


async def login(client: httpx.AsyncClient) -> dict:
    """登录并返回 cookies + csrf。"""
    resp = await client.post(f"{BACKEND}/api/v1/auth/login", json={
        "email": EMAIL,
        "password": PASSWORD,
    })
    print(f"登录: {resp.status_code} {resp.text[:200]}")
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    cookies = resp.headers.get_list("set-cookie")
    csrf = None
    for c in cookies:
        if "csrf_token=" in c:
            csrf = c.split("csrf_token=")[1].split(";")[0]
    return {"csrf": csrf}


async def register_local_path(client: httpx.AsyncClient, csrf: str) -> dict:
    """注册本地路径授权，返回 setup_command。"""
    resp = await client.post(
        f"{BACKEND}/api/v1/local-paths/register",
        json={
            "local_path": LOCAL_PATH,
            "scope": "read_write",
            "label": "E2E 本地目录",
        },
        headers={"X-CSRF-Token": csrf},
    )
    print(f"注册授权: {resp.status_code} {resp.text[:300]}")
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    data = resp.json()["data"]
    return {
        "grant_id": data["grant"]["id"],
        "setup_command": data["setup_command"],
        "setup_token": data["setup_token"],
    }


async def main():
    user_id = await create_enterprise_and_user()

    async with httpx.AsyncClient(timeout=30) as client:
        auth = await login(client)
        csrf = auth["csrf"]
        grant_info = await register_local_path(client, csrf)
        grant_id = grant_info["grant_id"]
        setup_command = grant_info["setup_command"]
        print(f"\n授权 ID: {grant_id}")
        print(f"Setup 命令:\n{setup_command}")

        # 启动本地 Runner（使用已构建的 bin 脚本）
        env = os.environ.copy()
        node_exe = os.environ.get("NODE_EXE", "node")
        runner = subprocess.Popen(
            [node_exe, os.path.join(RUNNER_DIR, "bin", "autoteams-runner.js"),
             "connect",
             "--server", "ws://127.0.0.1:3001/bridge",
             "--token", grant_info["setup_token"],
             "--grant", grant_id,
             "--path", LOCAL_PATH,
             "--scope", "read_write"],
            cwd=RUNNER_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        print(f"Runner 进程已启动 PID={runner.pid}")

        # 轮询授权状态直到 connected
        connected = False
        for _ in range(60):
            resp = await client.get(
                f"{BACKEND}/api/v1/local-paths/{grant_id}",
                headers={"X-CSRF-Token": csrf},
            )
            if resp.status_code == 200:
                status = resp.json()["data"]["status"]
                print(f"  授权状态: {status}")
                if status == "connected":
                    connected = True
                    break
            await asyncio.sleep(1)
        assert connected, "Runner 未能连接云端（授权未变为 connected）"

        print("\n✅ Runner 已连接！")

        # 执行 list 任务
        resp = await client.post(
            f"{BACKEND}/api/v1/local-paths/{grant_id}/run",
            json={"tool": "list", "path": "."},
            headers={"X-CSRF-Token": csrf},
        )
        print(f"\n[list] {resp.status_code} {resp.text[:500]}")
        assert resp.status_code == 200, f"list 失败: {resp.text}"
        results.append(("list", resp.status_code))

        # 执行 read 任务
        resp = await client.post(
            f"{BACKEND}/api/v1/local-paths/{grant_id}/run",
            json={"tool": "read", "path": "docs/sample.txt"},
            headers={"X-CSRF-Token": csrf},
        )
        print(f"\n[read] {resp.status_code} {resp.text[:500]}")
        assert resp.status_code == 200, f"read 失败: {resp.text}"
        assert "hello from local e2e" in resp.text, f"read 内容不符: {resp.text}"
        results.append(("read", resp.status_code))

        # 执行 write 任务
        resp = await client.post(
            f"{BACKEND}/api/v1/local-paths/{grant_id}/run",
            json={"tool": "write", "path": "docs/written.txt", "content": "写入测试内容\n"},
            headers={"X-CSRF-Token": csrf},
        )
        print(f"\n[write] {resp.status_code} {resp.text[:500]}")
        assert resp.status_code == 200, f"write 失败: {resp.text}"
        results.append(("write", resp.status_code))

        # 执行 cli 任务（echo 在白名单内）
        resp = await client.post(
            f"{BACKEND}/api/v1/local-paths/{grant_id}/run",
            json={"tool": "cli", "command": "echo hello-cli", "cwd": "."},
            headers={"X-CSRF-Token": csrf},
        )
        print(f"\n[cli] {resp.status_code} {resp.text[:500]}")
        assert resp.status_code == 200, f"cli 失败: {resp.text}"
        assert "hello-cli" in resp.text, f"cli 输出不符: {resp.text}"
        results.append(("cli", resp.status_code))

        # 验证 write 落盘
        written_path = os.path.join(LOCAL_PATH, "docs", "written.txt")
        assert os.path.exists(written_path), "write 未落盘"
        with open(written_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "写入测试内容" in content, f"write 内容不符: {content}"
        print("\n✅ write 已真实落盘到本地文件系统")

        # 清理：撤销授权
        resp = await client.delete(
            f"{BACKEND}/api/v1/local-paths/{grant_id}",
            headers={"X-CSRF-Token": csrf},
        )
        print(f"\n[revoke] {resp.status_code} {resp.text[:200]}")

        runner.terminate()

    print("\n" + "=" * 70)
    print("端到端测试完成！")
    for name, code in results:
        print(f"  ✅ {name}: HTTP {code}")


if __name__ == "__main__":
    asyncio.run(main())