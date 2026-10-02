# 会议总结助手 - 演示文件

本目录存放会议纪要与管理文档，由 `backend/scripts/generate_sample_data.py` 生成，
内容与 `seed_demo.py` 中的会议问答对保持一致。

## 文件清单

| 文件名 | 类型 | 内容摘要 |
|--------|------|---------|
| 会议纪要_产能规划.md | MD | Q3 产能提升 12%、SMT 瓶颈、责任人 |
| 会议纪要_质量复盘.md | MD | 来料不良率降至 0.8%、SPC 控制图 |
| 会议纪要_安全培训.md | MD | 三级安全教育、化学品泄漏应急 |
| 周会_生产进度.md | MD | 核心指标（达成率/OEE/合格率） |
| 项目启动会.md | MD | 4 个里程碑、关键风险 |
| 月度经营分析.xlsx | XLSX | 经营指标 + 决策事项（多 sheet） |
| 培训材料_新员工.md | MD | 培训内容、上岗流程、考核标准 |
| 改善提案汇总.pdf | PDF | 18 条提案、奖励机制 |

## 重新生成

```bash
cd backend
python -m scripts.generate_sample_data
```
