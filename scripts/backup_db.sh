#!/usr/bin/env bash
# ============================================================
# P1-08-A + P3-4: AutoTeams 数据库 + 上传文件备份脚本
#
# 支持两种模式：
# - Docker/PostgreSQL：通过 docker compose exec pg_dump 备份
# - 本地/SQLite：直接复制 .db 文件
#
# P3-4 安全加固：
# - 如果配置了 BACKUP_ENCRYPTION_KEY，备份归档会使用 GPG 对称加密
#   （AES256），生成 .tar.gz.gpg；未配置则保持原 .tar.gz。
# - 最终归档与 .sha256 checksum 文件分开存放，便于异地保存校验。
#
# 用法：
#   ./scripts/backup_db.sh              # 自动检测模式
#   ./scripts/backup_db.sh --sqlite     # 强制 SQLite 模式
#   ./scripts/backup_db.sh --postgres   # 强制 PostgreSQL 模式
#
# 环境变量：
#   BACKUP_DIR                  备份输出目录（默认 ./backups）
#   BACKUP_ENCRYPTION_KEY       GPG 对称加密密码（为空则不加密）
#   BACKUP_POSTGRES_CONTAINER   PostgreSQL 容器名（默认 autoteams-postgres-1）
#   BACKUP_POSTGRES_USER        PostgreSQL 用户名（默认 autoteams）
#   BACKUP_POSTGRES_DB          PostgreSQL 数据库名（默认 autoteams）
#   SQLITE_DB_PATH              SQLite 数据库路径（默认 ./backend/autoteams.db）
#   UPLOADS_DIR                 上传文件目录（默认 ./backend/uploads）
#   BACKUP_CHROMADB_CONTAINER   ChromaDB 容器名（默认 autoteams-chromadb-1）
#   CHROMA_PERSIST_DIR          嵌入式 ChromaDB 数据目录（默认 ./data/chroma）
#   BACKUP_SOURCE_QUIESCE_CMD  归档 ChromaDB 前执行的停写命令（默认空 = 不停写）
#   BACKUP_SOURCE_RESUME_CMD   归档后执行的恢复写入命令（默认空）
#   BACKUP_REQUIRE_SOURCE_QUIESCE  true 时缺少停写钩子即失败（默认 false）
# ============================================================
set -euo pipefail

# 失败时删除半成品工作目录，避免残留的目录被误当成一份备份。
BACKUP_WORK_DIR=""
BACKUP_DONE=false
on_exit() {
  rc=$?
  if [ "${BACKUP_DONE}" != true ]; then
    [ -z "${BACKUP_WORK_DIR}" ] || rm -rf "${BACKUP_WORK_DIR}"
    if [ "${rc}" -eq 0 ]; then
      rc=1
    fi
  fi
  exit "${rc}"
}
trap on_exit EXIT

# 校验 pg_dump 产物确实是一份可恢复的转储。
# 有效 gzip + 有效 sha256 并不能证明里面有数据库：AUD-20 的失效场景正是
# "pg_dump 失败 → gzip 收到空输入 → 归档看起来完好但没有数据"。
verify_pg_dump() {
  local file="$1"
  if [ ! -s "${file}" ]; then
    echo "错误：pg_dump 产物为空: ${file}" >&2
    return 1
  fi
  if ! grep -q "PostgreSQL database dump" "${file}"; then
    echo "错误：pg_dump 产物缺少 'PostgreSQL database dump' 头部标记: ${file}" >&2
    return 1
  fi
  if ! grep -qE "^(CREATE TABLE|COPY |CREATE SEQUENCE|ALTER TABLE ONLY)" "${file}"; then
    echo "错误：pg_dump 产物中没有表/数据定义，疑似不完整: ${file}" >&2
    return 1
  fi
  return 0
}

# 校验 gzip 容器（magic header + CRC 完整性）。
verify_gzip() {
  local file="$1"
  if [ ! -s "${file}" ]; then
    echo "错误：gzip 产物为空: ${file}" >&2
    return 1
  fi
  if ! gzip -t "${file}" 2>/dev/null; then
    echo "错误：gzip 完整性校验失败: ${file}" >&2
    return 1
  fi
  return 0
}

# 校验 SQLite 备份的 magic header（"SQLite format 3\0"）。
verify_sqlite() {
  local file="$1"
  if [ ! -s "${file}" ]; then
    echo "错误：SQLite 备份为空: ${file}" >&2
    return 1
  fi
  if ! head -c 16 "${file}" | grep -q "SQLite format 3"; then
    echo "错误：SQLite 备份 magic header 不匹配: ${file}" >&2
    return 1
  fi
  return 0
}

# 停写 / 恢复写入钩子（ChromaDB 处于运行中时提供真正的一致性保证）。
# 真正的卷快照需要宿主机 LVM/ZFS 权限；tar 运行中目录只是一次非一致性拷贝。
quiesce_chroma() {
  if [ -n "${BACKUP_SOURCE_QUIESCE_CMD}" ]; then
    echo "    quiesce: ${BACKUP_SOURCE_QUIESCE_CMD}"
    if ! sh -c "${BACKUP_SOURCE_QUIESCE_CMD}"; then
      echo "错误：BACKUP_SOURCE_QUIESCE_CMD 执行失败" >&2
      return 1
    fi
    return 0
  fi
  if [ "${BACKUP_REQUIRE_SOURCE_QUIESCE}" = true ]; then
    echo "错误：BACKUP_REQUIRE_SOURCE_QUIESCE=true 但未配置 BACKUP_SOURCE_QUIESCE_CMD" >&2
    return 1
  fi
  return 2
}

resume_chroma() {
  if [ -n "${BACKUP_SOURCE_RESUME_CMD}" ]; then
    echo "    resume: ${BACKUP_SOURCE_RESUME_CMD}"
    if ! sh -c "${BACKUP_SOURCE_RESUME_CMD}"; then
      echo "错误：BACKUP_SOURCE_RESUME_CMD 执行失败（写入可能仍处于暂停状态）" >&2
      return 1
    fi
  fi
  return 0
}

# 恢复自检：解开刚生成的归档，验证内部校验和与数据库文件。
# 归档没有通过自检之前，不写最终 .sha256 标记，也不允许轮转。
verify_archive_restorable() {
  local archive="$1"
  local name="$2"
  local probe
  probe=$(mktemp -d)

  echo "    恢复自检：解包 $(basename "${archive}")"
  if ! tar xzf "${archive}" -C "${probe}"; then
    echo "错误：恢复自检失败，归档无法解包" >&2
    rm -rf "${probe}"
    return 1
  fi
  if [ ! -f "${probe}/${name}/checksums.sha256" ]; then
    echo "错误：恢复自检失败，归档内缺少 checksums.sha256" >&2
    rm -rf "${probe}"
    return 1
  fi
  if ! (cd "${probe}/${name}" && sha256sum -c checksums.sha256 --quiet); then
    echo "错误：恢复自检失败，内部校验和不匹配" >&2
    rm -rf "${probe}"
    return 1
  fi
  if [ -f "${probe}/${name}/postgres_dump.sql.gz" ]; then
    if ! verify_gzip "${probe}/${name}/postgres_dump.sql.gz"; then
      rm -rf "${probe}"
      return 1
    fi
    if ! gunzip -c "${probe}/${name}/postgres_dump.sql.gz" > "${probe}/dump.sql"; then
      echo "错误：恢复自检失败，归档内数据库无法解压" >&2
      rm -rf "${probe}"
      return 1
    fi
    if ! verify_pg_dump "${probe}/dump.sql"; then
      rm -rf "${probe}"
      return 1
    fi
  elif [ -f "${probe}/${name}/autoteams.db" ]; then
    if ! verify_sqlite "${probe}/${name}/autoteams.db"; then
      rm -rf "${probe}"
      return 1
    fi
  else
    echo "错误：恢复自检失败，归档内既没有 postgres_dump.sql.gz 也没有 autoteams.db" >&2
    rm -rf "${probe}"
    return 1
  fi
  rm -rf "${probe}"
  echo "    恢复自检通过"
  return 0
}

# 加载 .env（如果存在）
if [ -f .env ]; then
  set -a; source .env; set +a
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"
if [ -z "${SQLITE_DB_PATH:-}" ]; then
  if [ -f ./backend/autoteams.db ] && [ -f ./backend/autofde.db ]; then
    echo '错误：新旧 SQLite 数据库同时存在；请显式设置 SQLITE_DB_PATH' >&2
    exit 1
  fi
  if [ -f ./backend/autofde.db ]; then
    SQLITE_DB_PATH=./backend/autofde.db
  else
    SQLITE_DB_PATH=./backend/autoteams.db
  fi
fi
UPLOADS_DIR="${UPLOADS_DIR:-./backend/uploads}"
POSTGRES_CONTAINER="${BACKUP_POSTGRES_CONTAINER:-autoteams-postgres-1}"
POSTGRES_USER="${BACKUP_POSTGRES_USER:-${POSTGRES_USER:-autoteams}}"
POSTGRES_DB="${BACKUP_POSTGRES_DB:-${POSTGRES_DB:-autoteams}}"
CHROMADB_CONTAINER="${BACKUP_CHROMADB_CONTAINER:-autoteams-chromadb-1}"
CHROMA_PERSIST_DIR="${CHROMA_PERSIST_DIR:-./data/chroma}"
BACKUP_SOURCE_QUIESCE_CMD="${BACKUP_SOURCE_QUIESCE_CMD:-}"
BACKUP_SOURCE_RESUME_CMD="${BACKUP_SOURCE_RESUME_CMD:-}"
BACKUP_REQUIRE_SOURCE_QUIESCE="${BACKUP_REQUIRE_SOURCE_QUIESCE:-false}"

MODE="${1:-auto}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_PATH="${BACKUP_DIR}/autoteams_${TIMESTAMP}"

mkdir -p "${BACKUP_PATH}"
BACKUP_WORK_DIR="${BACKUP_PATH}"

echo "============================================================"
echo "AutoTeams 备份开始 - ${TIMESTAMP}"
echo "备份目录: ${BACKUP_PATH}"
if [ -n "${BACKUP_ENCRYPTION_KEY}" ]; then
  echo "加密: 已启用（GPG 对称加密 AES256）"
else
  echo "加密: 未启用（未配置 BACKUP_ENCRYPTION_KEY）"
fi
echo "============================================================"

# 检测模式
if [ "${MODE}" = "--postgres" ]; then
  MODE="postgres"
elif [ "${MODE}" = "--sqlite" ]; then
  MODE="sqlite"
elif [ "${MODE}" = "auto" ]; then
  # 自动检测：Docker 容器存在则用 PostgreSQL，否则检查 SQLite 文件
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${POSTGRES_CONTAINER}"; then
    MODE="postgres"
  elif [ -f "${SQLITE_DB_PATH}" ]; then
    MODE="sqlite"
  else
    echo "错误：无法自动检测数据库模式"
    echo "  - PostgreSQL 容器 '${POSTGRES_CONTAINER}' 未运行"
    echo "  - SQLite 文件 '${SQLITE_DB_PATH}' 不存在"
    echo "请用 --postgres 或 --sqlite 参数指定模式"
    exit 1
  fi
fi

echo "备份模式: ${MODE}"

# -------------------- 数据库备份 --------------------
# AUD-20：`pg_dump | gzip` 的退出码是 gzip 的，pg_dump 失败会被完全掩盖，
# 产出"有 gzip 头、有 sha256、但没有数据库"的假备份。
# 改为：pg_dump 写普通文件 → 检查退出码 → 校验内容 → 再 gzip。
if [ "${MODE}" = "postgres" ]; then
  DB_FILE="${BACKUP_PATH}/postgres_dump.sql.gz"
  DUMP_RAW="${BACKUP_PATH}/postgres_dump.sql"
  echo "[1/4] 备份 PostgreSQL 数据库..."
  if ! docker ps --format '{{.Names}}' | grep -q "${POSTGRES_CONTAINER}"; then
    echo "错误：PostgreSQL 容器 '${POSTGRES_CONTAINER}' 未运行"
    exit 1
  fi
  if ! docker exec "${POSTGRES_CONTAINER}" \
       pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --no-owner --no-privileges \
       > "${DUMP_RAW}"; then
    echo "错误：pg_dump 失败（凭据 / 连接 / 容器内工具任一原因）" >&2
    exit 1
  fi
  if ! verify_pg_dump "${DUMP_RAW}"; then
    exit 1
  fi
  if ! gzip -n -c "${DUMP_RAW}" > "${DB_FILE}"; then
    echo "错误：gzip 压缩失败" >&2
    exit 1
  fi
  rm -f "${DUMP_RAW}"
  if ! verify_gzip "${DB_FILE}"; then
    exit 1
  fi
  echo "  ✓ 数据库备份: ${DB_FILE} ($(du -h "${DB_FILE}" | cut -f1))"
else
  DB_FILE="${BACKUP_PATH}/autoteams.db"
  echo "[1/4] 备份 SQLite 数据库..."
  if [ ! -f "${SQLITE_DB_PATH}" ]; then
    echo "错误：SQLite 文件 '${SQLITE_DB_PATH}' 不存在"
    exit 1
  fi
  # 使用 sqlite3 的 .backup 命令确保一致性（如果 sqlite3 可用）
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "${SQLITE_DB_PATH}" ".backup '${DB_FILE}'"
  else
    # 降级为直接复制（WAL 可能未合并，恢复时需要同目录的 -wal/-shm）
    cp "${SQLITE_DB_PATH}" "${DB_FILE}"
  fi
  if ! verify_sqlite "${DB_FILE}"; then
    exit 1
  fi
  echo "  ✓ 数据库备份: ${DB_FILE} ($(du -h "${DB_FILE}" | cut -f1))"
fi

# -------------------- 上传文件备份 --------------------
echo "[2/4] 备份上传文件..."
if [ -d "${UPLOADS_DIR}" ]; then
  tar czf "${BACKUP_PATH}/uploads.tar.gz" -C "$(dirname "${UPLOADS_DIR}")" "$(basename "${UPLOADS_DIR}")"
  echo "  ✓ 上传文件备份: ${BACKUP_PATH}/uploads.tar.gz ($(du -h "${BACKUP_PATH}/uploads.tar.gz" | cut -f1))"
else
  echo "  - 上传文件目录不存在，跳过"
fi

# -------------------- ChromaDB 备份 (DB-05) --------------------
# 一致性协调（AUD-20）：ChromaDB 运行时直接 tar 其数据目录只是"某一瞬间的
# 文件视图"，可能包含半写入的段/索引。真正的卷快照需要宿主机 LVM/ZFS 权限，
# 这里不假装能做到：提供可配置的停写/恢复钩子，并把实际达到的一致性级别
# 写进归档内的 manifest.txt，让恢复方知道这份向量数据可不可信。
echo "[3/4] 备份 ChromaDB 向量数据..."
CHROMA_BACKED_UP=false
CHROMA_CONSISTENCY="not-captured"
CHROMA_CONTAINER_RUNNING=false
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${CHROMADB_CONTAINER}"; then
  CHROMA_CONTAINER_RUNNING=true
fi
if [ "${CHROMA_CONTAINER_RUNNING}" = true ] || [ -d "${CHROMA_PERSIST_DIR}" ]; then
  if quiesce_chroma; then
    CHROMA_CONSISTENCY="quiesced"
  else
    quiesce_rc=$?
    if [ "${quiesce_rc}" -ne 2 ]; then
      exit 1
    fi
    CHROMA_CONSISTENCY="hot-copy-uncoordinated"
    echo "  ⚠ 未配置停写钩子：ChromaDB 为运行中目录的非一致性拷贝"
  fi
fi
# 优先检测 Docker 容器模式（ChromaDB 作为独立服务运行）
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${CHROMADB_CONTAINER}"; then
  echo "  检测到 ChromaDB 容器 '${CHROMADB_CONTAINER}'，从容器卷备份数据..."
  # IS_PERSISTENT=TRUE 保证写入即落盘；直接 tar 容器内 /chroma/chroma 目录
  if docker exec "${CHROMADB_CONTAINER}" tar czf - -C /chroma chroma 2>/dev/null > "${BACKUP_PATH}/chromadb.tar.gz"; then
    echo "  ✓ ChromaDB 备份: ${BACKUP_PATH}/chromadb.tar.gz ($(du -h "${BACKUP_PATH}/chromadb.tar.gz" | cut -f1))"
    CHROMA_BACKED_UP=true
  else
    echo "  ⚠ ChromaDB 容器备份失败，将尝试嵌入式目录"
  fi
fi
# 嵌入式模式：ChromaDB 以 PersistentClient 运行，数据存储在本地 CHROMA_PERSIST_DIR
if [ "${CHROMA_BACKED_UP}" = false ] && [ -d "${CHROMA_PERSIST_DIR}" ]; then
  echo "  检测到嵌入式 ChromaDB 目录 '${CHROMA_PERSIST_DIR}'，直接打包..."
  tar czf "${BACKUP_PATH}/chromadb.tar.gz" -C "$(dirname "${CHROMA_PERSIST_DIR}")" "$(basename "${CHROMA_PERSIST_DIR}")"
  echo "  ✓ ChromaDB 备份: ${BACKUP_PATH}/chromadb.tar.gz ($(du -h "${BACKUP_PATH}/chromadb.tar.gz" | cut -f1))"
  CHROMA_BACKED_UP=true
fi
if [ "${CHROMA_BACKED_UP}" = false ]; then
  echo "  - 未检测到 ChromaDB 数据（无运行容器且目录 '${CHROMA_PERSIST_DIR}' 不存在），跳过"
fi
if ! resume_chroma; then
  exit 1
fi

# 记录本次实际达到的一致性级别，恢复时可据此判断向量数据可信度。
cat > "${BACKUP_PATH}/manifest.txt" <<EOF
backup_timestamp=${TIMESTAMP}
backup_mode=${MODE}
chroma_captured=${CHROMA_BACKED_UP}
chroma_consistency=${CHROMA_CONSISTENCY}
chroma_quiesce_cmd=${BACKUP_SOURCE_QUIESCE_CMD:-<none>}
EOF

# -------------------- 内部校验和 --------------------
echo "[4/4] 生成内部校验和..."
(cd "${BACKUP_PATH}" && find . -type f ! -name "checksums.sha256" -exec sha256sum {} \; > checksums.sha256)
echo "  ✓ 内部校验和: ${BACKUP_PATH}/checksums.sha256"

# -------------------- 归档 + 恢复自检 --------------------
# 归档必须真的能解开、校验和匹配、且内含可恢复的数据库，才允许写成功标记。
ARCHIVE="${BACKUP_DIR}/autoteams_${TIMESTAMP}.tar.gz"
echo "打包归档..."
if ! tar czf "${ARCHIVE}" -C "${BACKUP_DIR}" "autoteams_${TIMESTAMP}"; then
  echo "错误：打包归档失败" >&2
  exit 1
fi
if ! verify_archive_restorable "${ARCHIVE}" "autoteams_${TIMESTAMP}"; then
  echo "错误：归档未通过恢复自检，丢弃该归档（不写成功标记）" >&2
  rm -f "${ARCHIVE}"
  exit 1
fi
rm -rf "${BACKUP_PATH}"
BACKUP_WORK_DIR=""

# -------------------- 加密（P3-4）--------------------
FINAL_ARCHIVE="${ARCHIVE}"
if [ -n "${BACKUP_ENCRYPTION_KEY}" ]; then
  if ! command -v gpg &>/dev/null; then
    echo "错误：已配置 BACKUP_ENCRYPTION_KEY，但系统未安装 gpg"
    exit 1
  fi
  ENCRYPTED_ARCHIVE="${ARCHIVE}.gpg"
  echo "加密归档..."
  gpg --batch --yes --pinentry-mode loopback --passphrase "${BACKUP_ENCRYPTION_KEY}" \
      --symmetric --cipher-algo AES256 \
      --compress-algo 0 \
      --output "${ENCRYPTED_ARCHIVE}" "${ARCHIVE}"
  rm -f "${ARCHIVE}"
  FINAL_ARCHIVE="${ENCRYPTED_ARCHIVE}"
  echo "  ✓ 加密完成: ${FINAL_ARCHIVE}"
fi

# -------------------- 最终校验和（P3-4）--------------------
# 成功标记只在这里写：此时归档已通过恢复自检。
CHECKSUM_FILE="${FINAL_ARCHIVE}.sha256"
echo "生成最终归档校验和..."
(cd "${BACKUP_DIR}" && sha256sum "$(basename "${FINAL_ARCHIVE}")" > "$(basename "${CHECKSUM_FILE}")")
echo "  ✓ 校验和: ${CHECKSUM_FILE}"

BACKUP_DONE=true

echo "============================================================"
echo "备份完成: ${FINAL_ARCHIVE}"
echo "校验和: ${CHECKSUM_FILE}"
echo "大小: $(du -h "${FINAL_ARCHIVE}" | cut -f1)"
echo "============================================================"
echo "恢复命令: ./scripts/restore_db.sh ${FINAL_ARCHIVE}"
