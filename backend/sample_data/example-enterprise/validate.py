#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality validation for example-enterprise data."""
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

def read_csv(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return list(csv.DictReader(f))

results = []

def check(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f" -- {detail}"
    results.append((passed, line))
    print(line)

# 1. Customer ID consistency
customers = read_csv("05-crm/customers.csv")
cust_ids = {c["customer_id"] for c in customers}
contacts = read_csv("05-crm/contacts.csv")
opps = read_csv("05-crm/opportunities.csv")
acts = read_csv("05-crm/activities.csv")
orders = read_csv("06-orders/orders.csv")

ref_cust_ids = set()
for rows in [contacts, opps, acts, orders]:
    for r in rows:
        ref_cust_ids.add(r.get("customer_id", ""))
missing_cust = ref_cust_ids - cust_ids
check("Customer ID consistency", len(missing_cust) == 0,
      f"missing={missing_cust}" if missing_cust else f"all {len(cust_ids)} customers referenced")

# 2. Owner/employee ID consistency
org_ids = {
    "SL-2018-001","SL-2018-002","SL-2018-003","SL-2018-004","SL-2018-005",
    "SL-2018-006","SL-2018-007","SL-2019-015","SL-2019-020","SL-2019-025",
    "SL-2020-032","SL-2020-035","SL-2020-038","SL-2020-040","SL-2020-042",
    "SL-2021-045","SL-2021-048","SL-2021-050","SL-2021-052","SL-2022-058",
    "SL-2022-060","SL-2022-062","SL-2022-065","SL-2023-075"
}
owner_ids = set()
for r in customers:
    owner_ids.add(r["owner"])
for r in opps:
    owner_ids.add(r["owner"])
for r in acts:
    owner_ids.add(r["owner"])
for r in orders:
    owner_ids.add(r["sales_person"])
missing_owners = owner_ids - org_ids
check("Owner/employee ID consistency", len(missing_owners) == 0,
      f"missing={missing_owners}" if missing_owners else f"all {len(owner_ids)} owners valid")

# 3. Opportunity stages
quoting = [o for o in opps if o["stage"] == "报价中"]
approval = [o for o in opps if o["stage"] == "审批中"]
check("At least 1 quoting opportunity", len(quoting) >= 1,
      f"quoting={len(quoting)}, approval={len(approval)}")

# 4. Order product_id in price list
price_ids = {
    "SL-T100-RTU","SL-T100-MQTT","SL-T100-NBIOT","SL-P200-A","SL-P200-B","SL-P200-C",
    "SL-G300-1","SL-G300-2","SL-G300-4","SL-G300-EX","SL-GW500-STD","SL-GW500-LORA",
    "SL-GW500-DUAL","SL-GW500-UPS","SL-GW510-STD","SL-GW510-POE","SL-GW510-OUT",
    "SL-CT800-STD","SL-CT800-AI","SL-CT800-SIL2","SL-DC600-STD","SL-DC600-HSC","SL-DC600-UPS"
}
order_products = {o["product_id"] for o in orders}
missing_prods = order_products - price_ids
check("Order product_id in price list", len(missing_prods) == 0,
      f"missing={missing_prods}" if missing_prods else f"all {len(order_products)} product IDs valid")

# 5. Phone format
phone_re = re.compile(r'^1\d{2}-\d{4}-\d{4}$')
bad_phones = [c["phone"] for c in customers if not phone_re.match(c["phone"])]
check("Customer phone format (1XX-XXXX-XXXX)", len(bad_phones) == 0,
      f"bad={bad_phones}" if bad_phones else f"all {len(customers)} phones valid")

bad_contact_phones = [c["phone"] for c in contacts if not phone_re.match(c["phone"])]
check("Contact phone format (1XX-XXXX-XXXX)", len(bad_contact_phones) == 0,
      f"bad={bad_contact_phones}" if bad_contact_phones else f"all {len(contacts)} phones valid")

# 6. Email format
email_re = re.compile(r'^[^@]+@[^@]+\.[^@]+$')
bad_emails = [c["email"] for c in customers if not email_re.match(c["email"])]
check("Email format", len(bad_emails) == 0,
      f"bad={bad_emails}" if bad_emails else f"all {len(customers)} emails valid")

# 7. Contact associations
missing_contact_cust = [c for c in contacts if c["customer_id"] not in cust_ids]
check("Contact->customer association", len(missing_contact_cust) == 0,
      f"total contacts={len(contacts)}")

# 8. Activity associations
opp_ids = {o["opp_id"] for o in opps}
acts_with_opp = [a for a in acts if a.get("opp_id", "")]
missing_act_opp = [a for a in acts_with_opp if a["opp_id"] not in opp_ids]
check("Activity->opportunity association", len(missing_act_opp) == 0,
      f"total activities={len(acts)}, with_opp={len(acts_with_opp)}")

# 9. Order associations
missing_order_cust = [o for o in orders if o["customer_id"] not in cust_ids]
check("Order->customer association", len(missing_order_cust) == 0,
      f"total orders={len(orders)}")
completed = [o for o in orders if o["status"] == "已完成"]
feedback = [o for o in completed if "客户反馈" in o.get("notes", "")]
check("Completed orders with feedback >= 2", len(feedback) >= 2,
      f"completed={len(completed)}, with_feedback={len(feedback)}")

# 10. Customer distribution
s_count = sum(1 for c in customers if c["customer_level"] == "S")
a_count = sum(1 for c in customers if c["customer_level"] == "A")
b_count = sum(1 for c in customers if c["customer_level"] == "B")
c_count = sum(1 for c in customers if c["customer_level"] == "C")
check("Customer distribution (S=3,A=5,B=7,C=5)",
      s_count == 3 and a_count == 5 and b_count == 7 and c_count == 5,
      f"S={s_count},A={a_count},B={b_count},C={c_count}")

# 11. File statistics
all_files = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    for fn in filenames:
        if fn in ("validate.py", "validate.ps1"):
            continue
        all_files.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
md_count = sum(1 for f in all_files if f.endswith(".md"))
csv_count = sum(1 for f in all_files if f.endswith(".csv"))
check("File statistics", True,
      f"total={len(all_files)}, md={md_count}, csv={csv_count}")

# 12. SOP v1/v2 structural consistency
sop_v1 = open(os.path.join(ROOT, "02-sales/sales-sop-v1.md"), encoding='utf-8').read()
sop_v2 = open(os.path.join(ROOT, "02-sales/sales-sop-v2.md"), encoding='utf-8').read()
cs_v1 = open(os.path.join(ROOT, "03-customer-service/service-sop-v1.md"), encoding='utf-8').read()
cs_v2 = open(os.path.join(ROOT, "03-customer-service/service-sop-v2.md"), encoding='utf-8').read()
check("Sales SOP v1/v2 both have numbered steps",
      "步骤编号" in sop_v1 and "步骤编号" in sop_v2)
check("CS SOP v1/v2 both have numbered steps",
      "步骤编号" in cs_v1 and "步骤编号" in cs_v2)

# 13. No placeholder markers
placeholder_patterns = ["测试数据", "示例数据", "TODO", "TBD", "placeholder", "lorem"]
all_content = sop_v1 + sop_v2 + cs_v1 + cs_v2
for p in placeholder_patterns:
    if p.lower() in all_content.lower():
        check(f"No placeholder '{p}'", False, "found placeholder")
        break
else:
    check("No placeholder markers", True)

print()
print("=" * 60)
passed = sum(1 for p, _ in results if p)
total = len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL CHECKS PASSED")
    sys.exit(0)
else:
    print("SOME CHECKS FAILED")
    sys.exit(1)
