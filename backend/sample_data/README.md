# AutoTeams 演示数据目录

本目录存放复赛演示所需的示例文件，内容与 `backend/scripts/seed_demo.py`
中的问答对保持一致，确保评委实际触发文件处理流程时检索结果与演示对话匹配。

## 目录结构

```
sample_data/
├── README.md                 # 本说明
├── 生产助手/                # 制造业生产场景文档（10 个真实文件）
├── 技术问答助手/            # 技术文档与 API 参考（13 个真实文件）
└── 会议总结助手/            # 会议纪要与管理文档（8 个真实文件）
```

## 文件清单与类型

| Agent | 文件数 | 类型分布 |
|-------|--------|---------|
| 生产助手 | 10 | 8 PDF + 1 DOCX + 1 XLSX（跳过 1 JPG + 1 MP4） |
| 技术问答助手 | 13 | 8 PDF + 2 XLSX + 1 JSON + 1 PY + 1 YAML（跳过 1 PNG + 1 MP4） |
| 会议总结助手 | 8 | 6 MD + 1 XLSX + 1 PDF |

**生成方式**：`python -m scripts.generate_sample_data`（幂等，重复运行覆盖）

## 用途

1. **评委体验**：复赛评审会登录演示账号，查看知识库文件、发起对话、浏览监控面板。
2. **seed_demo.py**：`backend/scripts/seed_demo.py` 会依据本目录下的文件名在数据库中创建 `File` 记录。
3. **RAG 演示**：`backend/scripts/seed_vectors.py` 已预置与 QA 对匹配的向量，
   RAG 检索无需依赖文件解析；如需真实文件处理，将本目录文件复制到
   `backend/uploads/demo/<Agent 名称>/` 后触发处理任务。

## 关于 PDF 文件

PDF 使用 `generate_sample_data.py` 内置的最小化 PDF 写入器生成（无外部依赖），
以 base14 Helvetica 字体渲染 ASCII 摘要；完整中文内容已存放在：
- 同名 `.md` 文件（若存在）
- `seed_vectors.py` 预置向量片段

如需生成包含中文字体的 PDF，可安装 `reportlab` 并扩展生成器。

## 关于二进制媒体文件

`.jpg` / `.png` / `.mp4` 等二进制媒体文件未生成（生成器跳过），
原因：无法在不引入额外依赖的前提下生成有意义的二进制媒体内容。
这些文件在 `seed_demo.py` 中仍会创建 `File` 记录，KnowledgePage 列表
展示不受影响；如需真实媒体文件，请手动放置到对应 Agent 目录。

## 使用方式

### 生成文件

```bash
cd backend
python -m scripts.generate_sample_data
```

### 本地开发

```bash
cd backend
python -m scripts.seed_demo        # 创建数据库记录
python -m scripts.seed_vectors      # 预置 ChromaDB 向量
# 如需真实 RAG，将 sample_data/ 下文件复制到 uploads/demo/ 后触发处理
```

### Docker / Railway 部署

部署时 `migrate_and_start.sh` 会自动执行：
1. `alembic upgrade head`（迁移）
2. `python -m scripts.seed_demo`（幂等预置演示数据）
3. `python -m scripts.seed_vectors`（预置向量）

可通过环境变量 `SEED_SKIP=1` 跳过预置步骤。
