"""测试 chat/stream SSE 端点，便于脱离前端排查问题。"""
import httpx
import json

BASE_URL = "http://127.0.0.1:8000"
AGENT_ID = "1f6f76c7-bcf4-4f00-9e41-4b43f0fb66b0"


def main():
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        # 1. 登录获取 cookie
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "demo@autoteams.example", "password": "demo123456"},
        )
        print("LOGIN status:", login_resp.status_code)
        print("LOGIN cookies:", dict(login_resp.cookies))
        if login_resp.status_code != 200:
            print("LOGIN body:", login_resp.text)
            return

        csrf_token = login_resp.cookies.get("csrf_token")
        headers = {"Accept": "text/event-stream"}
        if csrf_token:
            headers["X-CSRF-Token"] = csrf_token

        # 2. 调用 SSE 流
        with client.stream(
            "POST",
            f"/api/v1/agents/{AGENT_ID}/chat/stream",
            json={"content": "你好", "conversation_id": None},
            headers=headers,
        ) as resp:
            print("STREAM status:", resp.status_code)
            print("STREAM headers:", dict(resp.headers))
            for line in resp.iter_lines():
                print("SSE line:", line)
                if line.startswith("data: "):
                    data = line[6:]
                    try:
                        parsed = json.loads(data)
                        print("  parsed:", parsed)
                    except json.JSONDecodeError:
                        pass


if __name__ == "__main__":
    main()
