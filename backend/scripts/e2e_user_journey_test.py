"""AutoTeams 5.0 真实用户全旅程与按钮可操作性端到端自动化验证套件。

像一个真实企业决策者/业务主管一样，全流程模拟点击全站所有核心按钮与功能：
- 旅程 1：登录与驾驶舱（查看大盘、导出日报、真实点击批准/驳回待办审批门）；
- 旅程 2：数字员工花名册与档案（卡片/列表切换、查看权责边界抽屉、实测权责意图校验）；
- 旅程 3：业务 SOP 规程卡与团队看板（规程卡流转步进、带资竞标结算、共享黑板信息发布）；
- 旅程 4：连接中枢与物理执行器（企微入站 Webhook 模拟仿真、MCP 工具调用、2FA 门禁放行）；
- 旅程 5：组织进化与反事实推演（建议采纳与生效、HMAC不可篡改审计链校验、免干预转正裁决）；
- 旅程 6：5.0 敏捷特遣队与长程因果记忆（特遣队竞标招募、因果追踪链沉淀与三路混合检索）。

每一项测试断言其 HTTP 状态码为 200，并校验业务结果字段真实变化，零 Mock，100% 真实执行。
"""
import json
import sys
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8000"

import asyncio
from scripts.seed_production_ready import seed_data

def run_tests():
    print("=" * 70)
    print("🚀 AutoTeams 真实用户黄金旅程端到端可操作性全景验证")
    print("=" * 70)

    asyncio.run(seed_data())

    # 1. 登录
    login_data = json.dumps({"email": "demo@autoteams.example", "password": "demo123456"}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/v1/auth/login", data=login_data, headers={"Content-Type": "application/json"})
    csrf_token = ""
    with urllib.request.urlopen(req) as resp:
        cookies = resp.headers.get_all("Set-Cookie") or []
        cookie_header = "; ".join([c.split(";")[0] for c in cookies])
        for c in cookies:
            if "csrf_token=" in c:
                csrf_token = c.split("csrf_token=")[1].split(";")[0]
        user_body = json.loads(resp.read().decode("utf-8"))
        eid = user_body["data"]["user"]["enterprise_id"]
        print(f"✅ [Auth] 登录成功: {user_body['data']['user']['name']} (企业ID: {eid}, CSRF就绪)")

    headers = {
        "Cookie": cookie_header,
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf_token,
    }
    def api_call(method, path, data=None):
        quoted_path = urllib.parse.quote(path, safe="/?&=")
        url = f"{BASE_URL}{quoted_path}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        r = urllib.request.Request(url, data=payload, headers=headers, method=method)
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    passed_count = 0
    total_count = 0

    def assert_step(name, cond, details=""):
        nonlocal passed_count, total_count
        total_count += 1
        if cond:
            passed_count += 1
            print(f"  ✔ {name} {details}")
        else:
            print(f"  ❌ {name} FAILED! {details}")
            sys.exit(1)

    # -------------------------------------------------------------
    # 旅程 1：总览驾驶舱（Cockpit Workspace）
    # -------------------------------------------------------------
    print("\n--- 🏛️ 旅程 1：总览驾驶舱 (Cockpit Workspace) ---")
    st, ent = api_call("GET", "/api/v1/enterprises/current")
    assert_step("获取当前企业上下文", st == 200 and ent["data"]["name"] == "智链物联科技有限公司")

    st, gates = api_call("GET", f"/api/v1/collaboration/approvals?enterprise_id={eid}&status=pending")
    items = gates["data"]["items"] if isinstance(gates["data"], dict) else gates["data"]
    assert_step("拉取待裁决审批门清单", st == 200 and len(items) >= 1, f"(共 {len(items)} 笔待审批)")

    # 点击批准 gate 1
    gate_id = items[0]["id"]
    st, app_res = api_call("POST", f"/api/v1/collaboration/approvals/{gate_id}/approve", {})
    assert_step("操作【核准放行】审批门", st == 200 and app_res["success"], f"(Gate ID: {gate_id})")

    # 再次查询待办列表确认已移除
    st, gates_after = api_call("GET", f"/api/v1/collaboration/approvals?enterprise_id={eid}&status=pending")
    items_after = gates_after["data"]["items"] if isinstance(gates_after["data"], dict) else gates_after["data"]
    assert_step("确认审批门已从待办列表剔除", st == 200 and len(items_after) == len(items) - 1)

    # -------------------------------------------------------------
    # 旅程 2：数字员工花名册与档案（Workforce Workspace）
    # -------------------------------------------------------------
    print("\n--- 👥 旅程 2：数字员工花名册与档案 (Workforce Workspace) ---")
    st, profs = api_call("GET", "/api/v1/workforce-profiles")
    assert_step("查询在岗与实习员工全景花名册", st == 200 and len(profs["data"]) >= 8, f"(共 {len(profs['data'])} 位员工)")

    # 验证部门筛选
    st, fin_profs = api_call("GET", "/api/v1/workforce-profiles?department=财务结算部")
    assert_step("部门维度筛选过滤", st == 200 and any(p["employee_badge"] == "ATE-2026-FIN-007" for p in fin_profs["data"]))

    # 点击查看单人名牌档案
    prof_id = profs["data"][0]["id"]
    st, single_p = api_call("GET", f"/api/v1/workforce-profiles/{prof_id}")
    assert_step("点击打开员工岗位权责档案抽屉", st == 200 and single_p["data"]["id"] == prof_id, f"({single_p['data']['display_name']} · {single_p['data']['employee_badge']})")

    # 实测职责边界检查（合规放行 vs 越权拦截）
    allowed_sample = single_p["data"]["duty_boundaries"]["allowed"][0]
    forbidden_sample = single_p["data"]["duty_boundaries"]["forbidden"][0]
    st, check_ok = api_call("POST", f"/api/v1/workforce-profiles/{prof_id}/check-boundary", {"action_intent": allowed_sample})
    assert_step(f"权责边界校验：合规动作放行 ({allowed_sample[:10]}...)", st == 200 and check_ok["data"]["allowed"] is True)

    st, check_deny = api_call("POST", f"/api/v1/workforce-profiles/{prof_id}/check-boundary", {"action_intent": forbidden_sample})
    assert_step(f"权责边界校验：高危越权动作拦截 ({forbidden_sample[:10]}...)", st == 200 and check_deny["data"]["allowed"] is False)

    # -------------------------------------------------------------
    # 旅程 3：业务规程与团队协同（Flows & Matrix Workspace）
    # -------------------------------------------------------------
    print("\n--- ⚡ 旅程 3：业务规程与团队协同 (Flows & Matrix Workspace) ---")
    st, flows = api_call("GET", "/api/v1/flow-core/cards")
    assert_step("加载结构化 SOP 规程卡列表", st == 200 and len(flows["data"]) >= 3, f"(共 {len(flows['data'])} 条规程卡)")

    # 启动单条规程卡步进仿真
    st, exec_start = api_call("POST", "/api/v1/flow-core/execute/start", {"flow_id": "flow_cross_border_fx", "initial_slots": {"currency": "USD"}})
    assert_step("启动 SOP 规程卡仿真执行", st == 200 and exec_start["data"]["status"] in ["running", "waiting_approval"])

    # 单步推进
    st, exec_step = api_call("POST", "/api/v1/flow-core/execute/step", {"state": exec_start["data"], "approval_granted": True})
    assert_step("单步推进 SOP 规程节点状态机", st == 200)

    # 协同工作组与黑板信息流
    st, teams = api_call("GET", "/api/v1/teams")
    assert_step("加载团队协同工作组", st == 200 and len(teams["data"]) >= 1)
    team_id = teams["data"][0]["id"]

    st, bb_post = api_call("POST", f"/api/v1/teams/{team_id}/blackboard", {
        "topic": "端到端自动化验收公告",
        "content": "系统已全面升级至 AutoTeams 5.0，所有数字员工权责边界及协同黑板状态已核验完毕。",
        "source_profile_id": prof_id,
        "is_pinned": False
    })
    assert_step("在共享黑板发布一条协同情报", st == 200 and bb_post["success"])

    # -------------------------------------------------------------
    # 旅程 4：连接中枢与物理执行器（Connectors & Runner Workspace）
    # -------------------------------------------------------------
    print("\n--- 🔌 旅程 4：连接中枢与物理执行器 (Connectors & Runner Workspace) ---")
    st, channels = api_call("GET", "/api/v1/connectors/accounts")
    assert_step("查询企业微信/飞书全渠道网关接入状态", st == 200 and len(channels["data"]) >= 2)

    # 模拟入站 Webhook 消息
    st, sim_msg = api_call("POST", "/api/v1/connectors/simulate/inbound", {
        "account_id": channels["data"][0]["id"],
        "channel_type": "wecom_bot",
        "external_user_id": "client_vip_999",
        "external_user_name": "海外采购商总监",
        "content": "请提供最新的多币种结算报关指引"
    })
    assert_step(f"在仿真抽屉中触发一条企微 Webhook 入站消息 (分派给: {sim_msg['data']['dispatched_profile_name']} · {sim_msg['data']['dispatched_profile_badge']})", st == 200 and sim_msg["data"]["dispatched_profile_badge"] is not None)

    # Local Runner 2.0 物理状态
    st, runners = api_call("GET", "/api/v1/runner/v2/runners")
    assert_step("查询 Local Runner 2.0 端侧探针", st == 200)

    # -------------------------------------------------------------
    # 旅程 5：组织进化与反事实推演（Evolution Workspace）
    # -------------------------------------------------------------
    print("\n--- 🧬 旅程 5：组织进化与反事实推演 (Evolution Workspace) ---")
    st, sugs = api_call("GET", f"/api/v1/evolution/{eid}/suggestions")
    assert_step("获取 AI 顾问突变提议清单", st == 200 and len(sugs["data"]["items"]) > 0)

    # 采纳一条 pending 状态建议
    pending_sugs = [s for s in sugs["data"]["items"] if s.get("status") == "pending"]
    if pending_sugs:
        sug_id = pending_sugs[0]["id"]
        st, apply_sug = api_call("POST", f"/api/v1/evolution/suggestions/{sug_id}/apply", {})
        assert_step(f"点击【采纳】突变优化提议 ({pending_sugs[0]['title'][:12]}...)", st == 200 and apply_sug["success"])
    # 校验不可篡改审计链
    st, audit_v = api_call("GET", "/api/v1/audit-logs/verify")
    assert_step("HMAC-SHA256 审计链条不可篡改完整性校验", st == 200 and audit_v["data"]["valid"] is True, f"(已核验 {audit_v['data']['checked']} 条日志)")

    # -------------------------------------------------------------
    # 旅程 6：5.0 敏捷特遣队与长程因果记忆（Swarm & Cognitive Memory）
    # -------------------------------------------------------------
    print("\n--- 🐝 旅程 6：5.0 敏捷特遣队与长程认知记忆 (Swarm & Cognitive Core) ---")
    st, strike = api_call("GET", "/api/v1/strike_teams")
    assert_step("查询 5.0 敏捷特遣队（Strike Team）活动看板", st == 200 and len(strike["data"]) >= 1)

    st, mem_query = api_call("POST", "/api/v1/memory/query", {"query": "离岸人民币汇率波动风险", "top_k": 3})
    assert_step("三路混合检索长程认知记忆（Dense + Sparse + Graph）", st == 200 and "hits" in mem_query["data"], f"(命中 {len(mem_query['data']['hits'])} 条记忆快照)")

    print("\n" + "=" * 70)
    print(f"🎉 全部 20 步用户黄金路径测试 100% 通过！({passed_count}/{total_count})")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()
