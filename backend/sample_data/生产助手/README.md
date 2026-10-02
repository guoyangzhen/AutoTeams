# 生产助手 - 演示文件

本目录存放制造业生产场景文档，由 `backend/scripts/generate_sample_data.py` 生成，
内容与 `seed_demo.py` 中的生产问答对保持一致。

## 文件清单

| 文件名 | 类型 | 内容摘要 |
|--------|------|---------|
| 生产手册.pdf | PDF | 产线 A 标准日产能 1,200 件，OEE 计算与交接班规范 |
| 质量标准.docx | DOCX | 不合格品处理流程、首件检验、SPC 控制图 |
| 工艺流程.pdf | PDF | 工艺变更流程、文件版本管理 |
| 设备操作规范.pdf | PDF | 三级保养、异常停线处理、LOTO 上锁挂牌 |
| 安全生产指南.pdf | PDF | 硬性规定、PPE 配置标准 |
| 物料清单.xlsx | XLSX | BOM 主表 + 安全库存（多 sheet） |
| 供应商合同.pdf | PDF | 付款条款、质量保证 |
| 生产计划表.xlsx | XLSX | Q3 主计划 + 换线计划（多 sheet） |
| 设备维护手册.pdf | PDF | 维护周期、MTBF/MTTR |
| 工艺变更通知.pdf | PDF | ECN-2026-07 主轴转速调整 |

**未生成**：质检记录.jpg、车间培训视频.mp4（二进制媒体，见上级 README 说明）

## 重新生成

```bash
cd backend
python -m scripts.generate_sample_data
```
