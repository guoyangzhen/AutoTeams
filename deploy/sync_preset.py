# -*- coding: utf-8 -*-
"""同步演示企业运行时数据 demo_runtime_preset.json：
1. 为每个流程引擎按「实际步骤动作」赋予唯一可读名称；
2. 去除步骤完全重复的流程引擎（避免流程引擎列表出现重复项）；
3. 运行底座 tool_registry 全部标记为已安装/已验证，并补充更基础的底座（LLM、向量库等）。
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE, "backend", "data", "demo_runtime_preset.json")

# 步骤签名 -> 业务名称（按实际流程动作命名）
STEP_NAME_MAP = {
    "接受任务|执行核心处理|结果审核|归档与记录": "通用业务处理流程",
    "线索获取与分配|客户需求沟通|方案与报价|成交与转交": "销售线索转化流程",
    "外观验收|通电验收|功能验收|验收报告签署": "产品验收流程",
    "接收报修|登记故障信息|故障分级|响应处理": "故障报修与响应流程",
    "确定客户等级|执行分级跟进策略|识别沉默客户|执行沉默客户唤醒流程": "客户分级与唤醒流程",
    "报修|远程诊断|返厂维修|维修周期|费用|返运": "返厂维修流程",
    "提交采购申请|供应商比价|下单与到货验收|入库归档": "采购申请流程",
    "录用确认|入职准备|入职登记|系统开通|入职培训": "员工入职流程",
    "离职申请|离职审批|工作交接|权限回收|离职结算": "员工离职流程",
}


def step_signature(engine: dict) -> str:
    names = [str(s.get("name", "")).strip() for s in engine.get("steps", [])]
    return "|".join(names)


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ---- 1&2. 流程引擎：去重 + 命名 ----
    engines = data.get("process_engines", [])
    seen = set()
    deduped = []
    for eng in engines:
        sig = step_signature(eng)
        if sig in seen:
            continue  # 步骤完全重复的只保留首个
        seen.add(sig)
        name = STEP_NAME_MAP.get(sig)
        if name:
            eng["name"] = name
        elif not eng.get("name"):
            eng["name"] = "业务流程"
        deduped.append(eng)
    data["process_engines"] = deduped
    print(f"process_engines: {len(engines)} -> {len(deduped)}")

    # ---- 3. 运行底座：全部已安装 + 补充更基础的底座 ----
    installed = True
    verified = True
    base_tools = [
        ("知识库检索", "知识库检索", "api"),
        ("文档解析", "文档解析", "api"),
        ("任务编排", "任务编排", "api"),
        ("审批流", "审批流", "api"),
        ("消息协作", "消息协作", "api"),
        ("报表生成", "报表生成", "api"),
        ("llm_service", "LLM 服务", "api"),
        ("vector_db", "向量数据库", "api"),
        ("db_query", "数据库查询", "api"),
        ("vector_search", "向量检索", "api"),
        ("memory_store", "记忆存储", "api"),
        ("auth_security", "身份与权限", "api"),
        ("monitoring", "监控告警", "api"),
    ]
    tool_registry = [
        {
            "tool_id": tid,
            "name": name,
            "tool_type": ttype,
            "installed": installed,
            "verified": verified,
            "config": {},
        }
        for tid, name, ttype in base_tools
    ]
    data["tool_registry"] = tool_registry
    print(f"tool_registry: {len(tool_registry)} tools, all installed/verified")

    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("written:", PATH)


if __name__ == "__main__":
    main()