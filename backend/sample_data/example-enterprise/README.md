# 示例企业预置数据集 — 智链物联（SmartLink IoT）

> **企业全称**：智链物联科技有限公司（SmartLink IoT Technology Co., Ltd.）
> **行业**：B2B IoT 智能硬件制造
> **规模**：约 120 人 / 年营收约 8000 万人民币
> **生成依据**：`docs/示例企业预置数据文档生成指南.md`
> **产品需求对齐**：`docs/AutoTeams项目需求重新梳理.md`（v3.0）
> **生成日期**：2026-07-28

---

## 1. 用途

本数据集为 AutoTeams 运行时演示提供一套**接近 100% 真实**的示例企业预置数据，支撑 MVP 六步闭环（Connect → Analyze → Interview → Compile → Deploy → Run → Evolve）与事件驱动多 Agent 业务协作场景的完整演示：

- **企业编译器输入**：公司简介、组织架构、SOP、产品资料、CRM 数据等作为五级编译器（Information → Knowledge → Process → Capability → Runtime）的原始输入。
- **AI 数字员工生成**：组织架构、岗位 KPI、权限说明用于岗位识别、能力矩阵构建与 AI 员工匹配。
- **业务运行演示**：CRM 商机（含"报价中"状态）、产品规格书、报价审批流程、FAQ 共同支撑"询盘 → 报价 → 审批 → 成交 → 售后"的事件驱动协作。
- **持续进化演示**：销售 SOP 与客服 SOP 均提供 v1（优化前）/ v2（优化后）两个版本，用于展示企业运行模型更新后 AI 员工自动同步进化。

---

## 2. 目录结构

```
example-enterprise/
├── README.md                          # 本说明
├── UNGENERATED.md                     # 未生成项及原因记录
├── validate.py                        # 数据质量校验脚本
├── 01-company/                        # 公司基础信息
│   ├── company-profile.md             #   公司简介（P0）
│   └── org-structure.md               #   组织架构 24 个关键岗位（P0）
├── 02-sales/                          # 销售模块
│   ├── sales-sop-v1.md                #   销售 SOP 优化前（P0）
│   ├── sales-sop-v2.md                #   销售 SOP 优化后（P0）
│   ├── quotation-template.md          #   报价单模板（P1）
│   └── faq.md                         #   销售 FAQ（P0）
├── 03-customer-service/               # 客服模块
│   ├── service-sop-v1.md              #   客服 SOP 优化前（P0）
│   ├── service-sop-v2.md              #   客服 SOP 优化后（P0）
│   ├── faq.md                         #   客服 FAQ（P0）
│   └── after-sales-process.md         #   售后服务流程（P1）
├── 04-products/                       # 产品资料
│   ├── product-catalog.md             #   产品目录 7 款产品（P0）
│   ├── price-list.md                  #   价格表 标准价/年折扣/大客户价（P0）
│   └── spec-sheets/                   #   规格书目录（P0）
│       ├── SL-T100.md                 #     温湿度传感器
│       ├── SL-P200.md                 #     压力传感器
│       ├── SL-G300.md                 #     气体传感器
│       ├── SL-GW500.md                #     工业网关
│       ├── SL-GW510.md                #     小型网关
│       ├── SL-CT800.md                #     智能控制器
│       └── SL-DC600.md                #     数据采集终端
├── 05-crm/                            # CRM 数据
│   ├── customers.csv                  #   客户 20 家（P0）
│   ├── contacts.csv                   #   联系人 30 条（P0）
│   ├── opportunities.csv              #   商机 10 条（P0）
│   └── activities.csv                 #   跟进记录 35 条（P0）
├── 06-orders/                         # 订单
│   └── orders.csv                     #   历史订单 20 条（P0）
├── 07-hr/                             # 人事
│   ├── hr-handbook.md                 #   HR 手册简化版（P1）
│   └── permissions.md                 #   RBAC 权限矩阵（P0）
├── 08-kpi/                            # 绩效
│   └── kpi-matrix.md                  #   岗位 KPI 矩阵（P0）
├── 09-finance/                        # 财务
│   └── approval-flow.md               #   报价审批流程图 v1/v2（P0）
├── 10-process/                        # 流程
│   └── flowcharts.md                  #   业务流程图（P1）
└── 11-contracts/                      # 合同
    └── contract-template.md           #   合同模板简化版（P1）
```

**文件统计**：共 30 个数据文件（25 Markdown + 5 CSV），合计约 211 KB。

---

## 3. 数据规模与分布

| 数据类别 | 数量 | 关键分布 |
|---------|------|---------|
| 关键岗位 | 24 人 | 覆盖 7 个部门（销售/产品/客服/财务/人事/研发/生产） |
| 产品 | 7 款 | 传感器 3 / 网关 2 / 控制器 1 / 终端 1 |
| 客户 | 20 家 | S 级 3 / A 级 5 / B 级 7 / C 级 5 |
| 联系人 | 30 条 | 每客户 1-2 个联系人，含决策者标记 |
| 商机 | 10 条 | 报价中 2 / 审批中 1 / 跟进中 2 / 已成交 3 / 已丢单 1 / 线索 1 |
| 跟进记录 | 35 条 | 全部关联商机，覆盖电话/邮件/拜访/线上会议/报价 |
| 历史订单 | 20 条 | 已完成 12（含 2 条客户反馈）/ 生产中 3 / 已发货 3 / 待生产 2 |
| 销售 FAQ | 15 条 | 涵盖通信协议/测温范围/网关容量/防护等级/质保等 |
| 客服 FAQ | 20 条 | 涵盖设备连接/数据异常/电池更换/固件升级/保修等 |

---

## 4. ID 命名规范

遵循生成指南 §6.1 的 ID 规范，确保跨文件引用一致：

| 实体 | ID 格式 | 示例 | 出现位置 |
|------|---------|------|---------|
| 员工 | `SL-YYYY-NNN` | `SL-2018-001` | org-structure / CRM.owner / orders.sales_person |
| 客户 | `C-NNN` | `C-001` | customers / contacts / opportunities / activities / orders |
| 联系人 | `CT-NNN` | `CT-001` | contacts / activities.contact_id |
| 商机 | `OPP-NNN` | `OPP-001` | opportunities / activities.opp_id |
| 跟进记录 | `ACT-NNN` | `ACT-001` | activities |
| 订单 | `ORD-YYYY-NNN` | `ORD-2024-001` | orders |
| 产品 | `SL-XNNN` | `SL-T100` | product-catalog / spec-sheets / price-list / orders |
| 产品型号 | `SL-XNNN-VAR` | `SL-T100-RTU` | price-list / orders.product_id |

---

## 5. 数据关联关系

```
组织架构（24 名员工）
  ├── 拥有 → 权限（按角色，permissions.md）
  ├── 负责 → CRM 客户（owner 字段）
  ├── 负责 → 商机（owner 字段）
  ├── 创建 → 跟进记录（owner 字段）
  ├── 关联 → 订单（sales_person 字段）
  └── 关联 → KPI（按岗位，kpi-matrix.md）

CRM 客户（20 家）
  ├── 拥有 → 联系人（30 条）
  ├── 拥有 → 商机（10 条）
  ├── 拥有 → 跟进记录（35 条）
  └── 拥有 → 订单（20 条）

产品（7 款，含 23 个型号）
  ├── 关联 → FAQ（关联产品字段）
  ├── 关联 → 报价单模板（产品明细）
  └── 关联 → 订单（product_id）

SOP（v1 + v2）
  ├── 关联 → 岗位（执行人字段）
  └── 关联 → 审批流程图（approval-flow.md）
```

---

## 6. SOP 前后对比设计

销售 SOP 与客服 SOP 均提供 v1（优化前）/ v2（优化后）两个版本，结构一致便于 diff 对比，用于演示"企业运行模型更新 → AI 员工自动同步进化"：

| 维度 | v1（优化前） | v2（优化后） | 演示效果 |
|------|-------------|-------------|---------|
| 产品参数查询 | 无标准流程 | 有标准流程，转产品/售前 | 销售 Agent 从失败到成功 |
| 报价审批 | 统一总监审批 | 金额分级（<5万经理 / 5-20万总监 / >20万 CEO） | 审批效率提升 |
| 询盘响应 | 无时效要求 | 4 小时首次响应 / 24 小时初步报价 | 响应速度提升 |
| 线索分配 | 无规则 | 按行业区域分配 | 线索转化率提升 |
| 丢单复盘 | 无 | 3 工作日内复盘 | 持续改进 |
| 客服分级 | 无分级 | L1/L2/L3 分级 | 客服效率提升 |
| 自动处理 | 无边界 | L1 自动处理 | 人工工作量降低 |

---

## 7. 演示场景数据匹配

### 7.1 事件驱动协作场景

核心演示场景"客户询盘 → 报价 → 审批 → 成交 → 售后"所需数据均已就位：

| 场景步骤 | 触发数据 | 关联文件 |
|---------|---------|---------|
| 收到新询盘 | CRM 商机（2 条"报价中"状态可触发） | opportunities.csv |
| 产品参数查询 | 产品规格书（7 份详细参数） | spec-sheets/*.md |
| 销售报价 | 价格表 + 报价单模板 | price-list.md / quotation-template.md |
| 财务审核 | 报价审批流程 v2 | approval-flow.md |
| 老板审批 | 金额分级审批（>20万 CEO） | approval-flow.md / permissions.md |
| 客服同步 | 客服 FAQ + 订单数据 | faq.md / orders.csv |
| 售后接管 | 售后服务流程 | after-sales-process.md |

### 7.2 企业访谈场景

访谈问题与对应数据均已就位（详见 `docs/AutoTeams项目需求重新梳理.md` §5.7）。

---

## 8. 真实性保障

- **行业术语**：使用 Modbus RTU / MQTT / LoRa / NB-IoT / IP65/IP67 / CE/FCC/RoHS 等真实 IoT 行业术语。
- **产品参数**：传感器测温范围 -40~125°C、精度 ±0.3°C 等符合真实水平。
- **价格区间**：传感器 500-3000 元 / 网关 3000-8000 元 / 控制器 5000-15000 元。
- **联系方式**：手机号统一 `1XX-XXXX-XXXX` 格式，企业邮箱使用 `@smartlink-iot.com` 域名。
- **客户命名**：使用真实行业+地域命名风格（如"华智制造科技有限公司"、"中海能源集团有限公司"）。
- **时间范围**：日期数据分布在 2023-2026 年，有合理时间间隔。
- **无占位符**：全数据集不含"测试数据"、"示例数据"、"TODO"、"TBD"等占位符标记（已通过校验脚本验证）。

---

## 9. 质量校验

### 9.1 运行校验脚本

```bash
cd backend/sample_data/example-enterprise
python validate.py
```

### 9.2 校验项（16 项全部通过）

| # | 校验项 | 结果 |
|---|--------|------|
| 1 | 客户 ID 一致性（被联系人/商机/跟进/订单引用） | PASS · 20 家客户全部被引用 |
| 2 | 负责人工号一致性（存在于组织架构） | PASS · 3 名销售工号全部有效 |
| 3 | 至少 1 条"报价中"商机（演示触发） | PASS · 报价中 2 / 审批中 1 |
| 4 | 订单 product_id 存在于价格表 | PASS · 11 个产品型号全部有效 |
| 5 | 客户手机号格式（1XX-XXXX-XXXX） | PASS · 20 个手机号全部合格 |
| 6 | 联系人手机号格式（1XX-XXXX-XXXX） | PASS · 30 个手机号全部合格 |
| 7 | 客户邮箱格式 | PASS · 20 个邮箱全部合格 |
| 8 | 联系人→客户关联 | PASS · 30 条联系人全部关联有效客户 |
| 9 | 跟进记录→商机关联 | PASS · 35 条跟进记录全部关联有效商机 |
| 10 | 订单→客户关联 | PASS · 20 条订单全部关联有效客户 |
| 11 | 已完成订单含客户反馈 ≥ 2 | PASS · 已完成 12 / 含反馈 2 |
| 12 | 客户分布（S=3,A=5,B=7,C=5） | PASS · 分布完全符合 |
| 13 | 文件统计 | PASS · 30 个文件（25 md + 5 csv） |
| 14 | 销售 SOP v1/v2 含步骤编号 | PASS |
| 15 | 客服 SOP v1/v2 含步骤编号 | PASS |
| 16 | 无占位符标记 | PASS |

---

## 10. 完成状态

| 优先级 | 应生成 | 已生成 | 完成率 |
|--------|--------|--------|--------|
| P0（必须详细完整） | 11 类 | 11 类 | **100%** |
| P1（需要但不需太详细） | 4 类 | 4 类 | **100%** |
| P2（可选或简化） | 5 类 | 0 类（按指南"可省略"处理，见 UNGENERATED.md） | — |

**P0 + P1 全部完成。** P2 数据按生成指南 §3 "可以少甚至没有"的规定，未生成，原因记录于 [UNGENERATED.md](UNGENERATED.md)。

---

## 11. 使用方式

### 11.1 作为 AutoTeams 编译器输入

将本目录作为"连接本地文件夹"数据源导入 AutoTeams，系统将依次执行 Connect → Analyze → Interview → Compile，产出 Enterprise Runtime。

### 11.2 配合种子脚本

如需将数据写入数据库用于演示，可参考 `backend/scripts/seed_demo.py` 的模式扩展导入逻辑；CSV 文件可直接被信息编译器（Information Compiler）解析为结构化信息。

### 11.3 演示 SOP 进化

1. 先使用 `02-sales/sales-sop-v1.md` + `03-customer-service/service-sop-v1.md` 编译企业运行模型。
2. 运行销售场景，观察销售 Agent 遇到产品参数问题时失败。
3. 切换为 `sales-sop-v2.md` + `service-sop-v2.md` 重新编译。
4. 重新运行销售场景，观察销售 Agent 自动转产品专家 Agent 后成功。
