# -*- coding: utf-8 -*-
"""清理 demo_runtime_preset.json 的组织架构数据。

背景：预设中的 organization.departments 混入了大量知识图谱/文档抽取的
伪部门节点（如「智链物联客服 SOP」「高管层」「华东区域服务中心」「销售部特殊」等），
且真实部门的 parent_dept_id 指向不存在的 dept_co_* 根，导致组织架构图
渲染错乱（节点堆在一条线上 / 根错误）。

本脚本将 departments 重写为一份干净、确定性的层级：
    公司根（level 0）→ 部门（level 1），parent_dept_id 均指向公司根。
与 buildOrgChart 的关键词推断表（销售/财务/客服/产品/人事行政/研发/生产）对齐，
确保各 AI 员工能正确归位到对应部门块下。

用法：
    cd backend
    python -m deploy.clean_org      # 或 python deploy/clean_org.py
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE, "backend", "data", "demo_runtime_preset.json")

COMPANY = "示例科技"

# (dept_id, name, parent_dept_id, level)
CLEAN_DEPARTMENTS: list[dict] = [
    {"dept_id": "dept_company", "name": COMPANY, "parent_dept_id": None, "head_employee_id": None, "level": 0},
    {"dept_id": "dept_sales", "name": "销售部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
    {"dept_id": "dept_finance", "name": "财务部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
    {"dept_id": "dept_cs", "name": "客服部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
    {"dept_id": "dept_product", "name": "产品部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
    {"dept_id": "dept_rd", "name": "研发部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
    {"dept_id": "dept_production", "name": "生产部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
    {"dept_id": "dept_hr", "name": "人事行政部", "parent_dept_id": "dept_company", "head_employee_id": None, "level": 1},
]


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    org = data.get("organization", {})
    old = org.get("departments", [])
    org["departments"] = CLEAN_DEPARTMENTS
    data["organization"] = org

    # reporting_tree 一并重建为确定性结构
    org["reporting_tree"] = {
        "root": "dept_company",
        "children": {d["dept_id"]: [] for d in CLEAN_DEPARTMENTS if d["parent_dept_id"] is None}
        or {d["dept_id"]: [] for d in CLEAN_DEPARTMENTS},
    }

    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"organization.departments: {len(old)} -> {len(CLEAN_DEPARTMENTS)}")
    print("written:", PATH)


if __name__ == "__main__":
    main()
