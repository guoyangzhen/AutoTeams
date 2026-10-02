#!/usr/bin/env bash
# ============================================================
# INF-DEP-02: 生产环境数据库迁移 + 启动包装脚本
#
# 用途：在 Railway/Docker 启动前安全地执行 Alembic 迁移并预置演示数据。
# 该脚本会：
# 1. 记录迁移前版本（用于失败时手动回滚）
# 2. 尝试执行 alembic upgrade head
# 3. 迁移失败时打印当前版本与建议回滚命令，并退出非零状态
#    阻止不健康容器进入服务状态
# 4. 迁移成功后幂等执行演示数据初始化（S3）：
#    - python -m scripts.seed_demo   （存在则跳过）
#    - python -m scripts.seed_vectors （重建 ChromaDB 集合）
#    两者失败仅告警，不阻塞应用启动；演示数据缺失仅影响评委体验，不健康。
#
# 使用方式：
#   railway.json startCommand: ./scripts/migrate_and_start.sh
#
# 注意事项：
# - Railway 托管 PostgreSQL 会自动做时间点恢复（PITR），但仍建议在重大
#   版本发布前手动执行 scripts/backup_db.sh 做逻辑备份。
# - 本脚本不做自动 downgrade，因为部分 DDL（如删除列/表）不可逆；失败时
#   需要运维人员根据当前版本决定是修复迁移脚本还是手动回滚。
# - L1: 使用 --no-server-header 隐藏 uvicorn Server 响应头，防止指纹识别
# - S3: seed_demo / seed_vectors 失败不阻塞启动；可在 SEED_SKIP=1 时跳过
# ============================================================
set -euo pipefail

# 加载 .env（如果存在）
if [ -f .env ]; then
  set -a; source .env; set +a
fi

# 1. 记录迁移前版本
CURRENT_REV=$(alembic current 2>/dev/null | awk '{print $1}' || echo "UNKNOWN")
echo "[migrate] 当前 Alembic 版本: ${CURRENT_REV}"

# 2. 执行真实迁移
# 说明：不在此处做 offline 干跑（alembic upgrade head --sql）。
#   1) 本仓库包含 DML 数据迁移（如 P2 的 relation/event_type 值改写、T22 RLS 策略），
#      这些迁移在 offline 干跑（MockConnection）下会执行数据库查询/取属性而失败，
#      导致干跑报错阻断启动，即使真实迁移本身是有效的。
#   2) Alembic 在线迁移默认在单个事务中执行，任一步失败会自动回滚，
#      且容器 depends_on 已确保 postgres 处于 healthy，可安全直接迁移。
echo "[migrate] 执行真实迁移 (alembic upgrade head) ..."
if ! alembic upgrade head; then
  echo "[migrate] 迁移失败。当前 Alembic 版本仍为: ${CURRENT_REV}"
  echo "[migrate] 如需回滚到该版本，请运行："
  echo "    alembic downgrade ${CURRENT_REV}"
  echo "[migrate] 或根据具体情况修复迁移脚本后重新部署。"
  exit 1
fi

# 4. 打印迁移后版本
NEW_REV=$(alembic current 2>/dev/null | awk '{print $1}' || echo "UNKNOWN")
echo "[migrate] 迁移完成。新版本: ${NEW_REV}"

# 5. 可选预置演示数据（失败不阻塞启动）
#    - 生产数据库默认绝不写入 demo 用户、对话或向量；如需评审/演示环境，必须显式
#      设置 SEED_DEMO=1。
#    - 保留 SEED_SKIP=1 兼容旧部署，它会优先禁用演示初始化。
if [ "${SEED_DEMO:-0}" != "1" ] || [ "${SEED_SKIP:-0}" = "1" ]; then
  echo "[seed] 默认跳过演示数据初始化；仅在隔离演示环境设置 SEED_DEMO=1 才会预置。"
else
  echo "[seed] SEED_DEMO=1，开始预置隔离演示数据 (idempotent) ..."
  if ! python -m scripts.seed_demo > /tmp/seed_demo.log 2>&1; then
    echo "[seed] 警告: seed_demo 执行失败，演示数据可能不完整。应用将继续启动。"
    echo "[seed] 失败详情（最后 20 行）："
    tail -n 20 /tmp/seed_demo.log 2>/dev/null || true
  else
    echo "[seed] seed_demo 完成。"
    tail -n 5 /tmp/seed_demo.log 2>/dev/null || true
  fi

  # seed_vectors 依赖 ChromaDB；嵌入式模式（CHROMA_HOST 为空）或独立服务模式下均可
  if ! python -m scripts.seed_vectors > /tmp/seed_vectors.log 2>&1; then
    echo "[seed] 警告: seed_vectors 执行失败，RAG 检索可能无引用来源。应用将继续启动。"
    echo "[seed] 失败详情（最后 20 行）："
    tail -n 20 /tmp/seed_vectors.log 2>/dev/null || true
  else
    echo "[seed] seed_vectors 完成。"
    tail -n 5 /tmp/seed_vectors.log 2>/dev/null || true
  fi
fi

# 6. 启动应用
echo "[migrate] 启动应用 ..."
# L1: --no-server-header 隐藏 Server: uvicorn 响应头，避免暴露组件指纹
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --no-server-header
