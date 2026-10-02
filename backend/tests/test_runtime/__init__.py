"""WT2 Enterprise Runtime 测试套件。

覆盖（验收标准对应）：
- test_runtime_store.py：Runtime 存储 CRUD + 语义化版本号（PRD §4.6 / §4.6.3）
- test_runtime_version.py：创建快照 / 回滚 / diff（PRD §4.6.3）
- test_runtime_query.py：RuntimeQueryInterface（spec §10.5 契约）
- test_runtime_api.py：API 端点（spec §10.7，无尾斜杠 + 分页 + 鉴权 + 限流）
- test_runtime_integration.py：WT1 编译产出 → 存储 → 查询 → diff → 回滚端到端
"""
