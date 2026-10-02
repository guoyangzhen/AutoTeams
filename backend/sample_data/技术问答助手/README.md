# 技术问答助手 - 演示文件

本目录存放技术文档与开发者资料，由 `backend/scripts/generate_sample_data.py` 生成，
内容与 `seed_demo.py` 中的技术问答对保持一致。

## 文件清单

| 文件名 | 类型 | 内容摘要 |
|--------|------|---------|
| API 参考文档.pdf | PDF | Bearer Token 认证、限流、Webhook、错误码 |
| SDK 使用指南.pdf | PDF | 5 种语言 SDK、Python 安装示例 |
| 部署手册.pdf | PDF | Docker Compose / K8s 部署、环境变量 |
| 数据库设计文档.pdf | PDF | PostgreSQL/MySQL/SQLite、核心表、多租户 |
| 架构设计文档.pdf | PDF | 三层架构、RAG 流水线 |
| 运维手册.pdf | PDF | 备份策略、日志位置 |
| 安全规范.pdf | PDF | JWT 认证、RBAC、X-Request-ID 排查 |
| 监控告警指南.pdf | PDF | Prometheus /metrics、告警规则 |
| 错误码列表.xlsx | XLSX | HTTP 状态码与错误码详表 |
| 性能基准测试.xlsx | XLSX | 压测结果 + 优化建议（多 sheet） |
| API 测试用例.json | JSON | 5 个测试用例 + 限流配置 |
| 部署脚本示例.py | Python | 一键 Docker Compose 部署脚本 |
| 配置文件模板.yaml | YAML | 生产环境配置模板 |

**未生成**：架构示意图.png、技术分享视频.mp4（二进制媒体，见上级 README 说明）

## 重新生成

```bash
cd backend
python -m scripts.generate_sample_data
```
