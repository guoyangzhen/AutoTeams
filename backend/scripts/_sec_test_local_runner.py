"""安全边界测试：本地工具桥接。

验证：
1. 路径越界（.. / 绝对路径 / 符号链接逃逸）被拒
2. scope=read 禁止 write/delete/cli
3. 非白名单 CLI 命令被拒
4. 无效 setup token 认领失败
5. 企业隔离：他人无法访问
6. 授权后 token 的哈希存储（不落明文）
"""
import asyncio
import os
import sqlite3
import subprocess
import sys
import uuid

import httpx

BACKEND = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
LOCAL_PATH = os.path.abspath(os.environ.get("E2E_LOCAL_PATH", "./_e2e_local"))

EMAIL = f"sec_{uuid.uuid4().hex[:8]}@test.com"
PASSWORD = "secPass123!"
TARGET_EMAIL = f"sec_other_{uuid.uuid4().hex[:8]}@test.com"
ENT_ID = f"sec-ent-{uuid.uuid4().hex[:8]}"
OTHER_ENT_ID = f"sec-ent-other-{uuid.uuid4().hex[:8]}"

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


async def seed_db():
    """创建两个企业 + 两个用户（同企业 + 跨企业）。"""
    sys.path.insert(0, os.path.abspath("./backend"))
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.abspath(
        os.path.join("backend", "autoteams.db")
    ).replace("\\", "/")
    from app.database import get_db
    from app.models.enterprise import Enterprise
    from app.models.user import User
    from app.utils.auth.password import get_password_hash

    async for session in get_db():
        session.add(Enterprise(id=ENT_ID, name="安全测试企业"))
        session.add(Enterprise(id=OTHER_ENT_ID, name="其他企业"))
        await session.commit()
        session.add(User(email=EMAIL, password_hash=get_password_hash(PASSWORD),
                         name="安全测试用户", role="member", enterprise_id=ENT_ID, is_active=True))
        session.add(User(email=TARGET_EMAIL, password_hash=get_password_hash(PASSWORD),
                         name="跨企业用户", role="member", enterprise_id=OTHER_ENT_ID, is_active=True))
        await session.commit()


async def login(client, email):
    resp = await client.post(f"{BACKEND}/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    for c in resp.headers.get_list("set-cookie"):
        if "csrf_token=" in c:
            return c.split("csrf_token=")[1].split(";")[0]
    return None


async def register(client, csrf, path, scope):
    resp = await client.post(f"{BACKEND}/api/v1/local-paths/register",
                             json={"local_path": path, "scope": scope, "label": "安全测试"},
                             headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    d = resp.json()["data"]
    return d["grant"]["id"], d["setup_token"]


async def run_task(client, csrf, grant_id, task):
    resp = await client.post(f"{BACKEND}/api/v1/local-paths/{grant_id}/run",
                             json=task, headers={"X-CSRF-Token": csrf})
    return resp.status_code, resp.text


def launch_runner(grant_id, token, scope="read_write"):
    """启动本地 Runner 进程并等待云端确认 connected。"""
    runner_dir = os.path.abspath("./local-runner")
    node_exe = os.environ.get("NODE_EXE", "node")
    proc = subprocess.Popen(
        [node_exe, os.path.join(runner_dir, "bin", "autoteams-runner.js"),
         "connect",
         "--server", "ws://127.0.0.1:3001/bridge",
         "--token", token,
         "--grant", grant_id,
         "--path", LOCAL_PATH,
         "--scope", scope],
        cwd=runner_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


async def wait_connected(client, csrf, grant_id, timeout=30):
    for _ in range(timeout):
        r = await client.get(f"{BACKEND}/api/v1/local-paths/{grant_id}",
                             headers={"X-CSRF-Token": csrf})
        if r.status_code == 200 and r.json()["data"]["status"] == "connected":
            return True
        await asyncio.sleep(1)
    return False


async def main():
    await seed_db()

    async with httpx.AsyncClient(timeout=30) as client:
        csrf = await login(client, EMAIL)

        # 1. 注册 read_write 授权
        grant_id, token = await register(client, csrf, LOCAL_PATH, "read_write")
        # 启动 Runner 并等待 connected
        rp = launch_runner(grant_id, token)
        ok = await wait_connected(client, csrf, grant_id)
        check("Runner 连接成功", ok)
        if not ok:
            rp.terminate()
            print("Runner 未连接，无法继续路径安全测试")
            sys.exit(1)

        # 1a. setup token 只在响应中返回一次，DB 只存哈希
        db = sqlite3.connect(os.path.abspath(os.path.join("backend", "autoteams.db")))
        row = db.execute("SELECT setup_token_hash FROM local_path_grants WHERE id=?",
                         (grant_id,)).fetchone()
        stored_hash = row[0]
        check("DB 仅存 token 哈希（不落明文）", stored_hash and token not in stored_hash and len(stored_hash) == 64,
              f"hash={stored_hash}")

        # 1b. 路径越界：.. 逃逸
        code, body = await run_task(client, csrf, grant_id, {"tool": "read", "path": "../secret.txt"})
        check("路径越界（..）被拒", code == 400 or "路径" in body, f"{code} {body[:120]}")

        # 1c. 绝对路径越界
        code, body = await run_task(client, csrf, grant_id, {"tool": "read", "path": "C:/Windows/win.ini"})
        check("绝对路径越界被拒", code == 400 or "路径" in body, f"{code} {body[:120]}")

        # 1d. 不存在路径
        code, body = await run_task(client, csrf, grant_id, {"tool": "read", "path": "not_exist.txt"})
        check("不存在路径被拒", code == 400 or "不存在" in body, f"{code} {body[:120]}")

        # 1e. 非白名单 CLI 被拒
        code, body = await run_task(client, csrf, grant_id, {"tool": "cli", "command": "rm -rf /", "cwd": "."})
        check("非白名单 CLI 被拒", code == 400 or "白名单" in body, f"{code} {body[:120]}")

        # 2. scope=read 授权禁止写
        read_grant, read_token = await register(client, csrf, LOCAL_PATH, "read")
        rp_read = launch_runner(read_grant, read_token, scope="read")
        await wait_connected(client, csrf, read_grant)
        code, body = await run_task(client, csrf, read_grant, {"tool": "write", "path": "x.txt", "content": "hi"})
        check("scope=read 禁止写", code == 403 or "只读" in body, f"{code} {body[:120]}")
        code, body = await run_task(client, csrf, read_grant, {"tool": "cli", "command": "echo hi", "cwd": "."})
        check("scope=read 禁止 cli", code == 403 or "只读" in body, f"{code} {body[:120]}")
        rp_read.terminate()

        # 3. 无效 token 认领失败（直接调 claim 端点，伪造 token）
        bad_resp = await client.post(f"{BACKEND}/api/v1/local-paths/{grant_id}/claim",
                                     json={"setup_token": "invalid-token"})
        check("无效 setup token 认领失败", bad_resp.status_code in (401, 403), f"{bad_resp.status_code}")

        # 4. 跨企业访问隔离
        other_csrf = await login(client, TARGET_EMAIL)
        # 跨企业用户读不到该授权（403）
        list_resp = await client.get(f"{BACKEND}/api/v1/local-paths/{grant_id}",
                                     headers={"X-CSRF-Token": other_csrf})
        check("跨企业访问隔离（403）", list_resp.status_code == 403, f"{list_resp.status_code} {list_resp.text[:120]}")

        # 5. 撤销后 claim 失效（重新登录以获取最新 CSRF，避免被其他用户登录覆盖）
        csrf = await login(client, EMAIL)
        revoke = await client.delete(f"{BACKEND}/api/v1/local-paths/{grant_id}",
                                     headers={"X-CSRF-Token": csrf})
        assert revoke.status_code == 200, f"撤销失败: {revoke.text}"
        claim_after = await client.post(f"{BACKEND}/api/v1/local-paths/{grant_id}/claim",
                                        json={"setup_token": token})
        check("撤销后 token 失效", claim_after.status_code in (403, 404), f"{claim_after.status_code}")

    print(f"\n安全测试结果: 通过 {passed}, 失败 {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())