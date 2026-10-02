#!/usr/bin/env bash
# ============================================================
# P1-08-B + P3-4: AutoTeams 数据库 + 上传文件恢复脚本
#
# 用法：
#   ./scripts/restore_db.sh <备份文件.tar.gz>
#   ./scripts/restore_db.sh <备份文件.tar.gz.gpg>
#   ./scripts/restore_db.sh backups/autoteams_20260714_120000.tar.gz
#
# 安全措施：
# - 交互式确认提示（除非传入 --yes 跳过）
# - 最终归档 SHA-256 校验和验证（P3-4）
# - GPG 对称解密（备份为 .gpg 且配置了 BACKUP_ENCRYPTION_KEY 时）
# - 恢复前自动备份当前数据
# - ChromaDB 向量数据恢复（Docker 容器卷或嵌入式目录，DB-05）
# ============================================================
set -euo pipefail

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

ARCHIVE="${1:-}"
SKIP_CONFIRM=false
if [ "${2:-}" = "--yes" ]; then
  SKIP_CONFIRM=true
fi

if [ -z "${ARCHIVE}" ]; then
  echo "用法: $0 <备份文件.tar.gz|备份文件.tar.gz.gpg> [--yes]"
  echo ""
  echo "可用备份："
  if [ -d "${BACKUP_DIR}" ]; then
    ls -lh "${BACKUP_DIR}"/autoteams_*.tar.gz* "${BACKUP_DIR}"/autofde_*.tar.gz* 2>/dev/null || echo "  （无备份文件）"
  else
    echo "  备份目录 ${BACKUP_DIR} 不存在"
  fi
  exit 1
fi

if [ ! -f "${ARCHIVE}" ]; then
  echo "错误：备份文件不存在: ${ARCHIVE}"
  exit 1
fi

echo "============================================================"
echo "AutoTeams 恢复开始"
echo "备份文件: ${ARCHIVE}"
echo "============================================================"

# -------------------- 最终校验和验证（P3-4）--------------------
ARCHIVE_BASENAME=$(basename "${ARCHIVE}")
CHECKSUM_FILE="${ARCHIVE}.sha256"

if [ -f "${CHECKSUM_FILE}" ]; then
  echo "[0/5] 验证最终归档校验和..."
  cd "$(dirname "${ARCHIVE}")"
  if ! sha256sum -c "$(basename "${CHECKSUM_FILE}")" --quiet 2>/dev/null; then
    echo "错误：最终归档校验和验证失败，备份文件可能已损坏或被篡改"
    exit 1
  fi
  cd - > /dev/null
  echo "  ✓ 最终校验和验证通过"
else
  echo "  - 未找到最终归档校验和文件 ${CHECKSUM_FILE}，跳过校验"
  echo "    建议将 .tar.gz(.gpg) 与 .sha256 文件一并异地保存"
fi

# -------------------- 解密（P3-4）--------------------
WORK_ARCHIVE="${ARCHIVE}"
NEED_DECRYPT=false
DECRYPTED=""
if [[ "${ARCHIVE}" == *.gpg ]]; then
  NEED_DECRYPT=true
  if [ -z "${BACKUP_ENCRYPTION_KEY}" ]; then
    echo "错误：备份文件已加密，但未配置 BACKUP_ENCRYPTION_KEY"
    exit 1
  fi
  if ! command -v gpg &>/dev/null; then
    echo "错误：需要 gpg 解密，但系统未安装 gpg"
    exit 1
  fi
  DECRYPTED=$(mktemp)
  echo "[1/5] 解密备份归档..."
  gpg --batch --yes --pinentry-mode loopback --passphrase "${BACKUP_ENCRYPTION_KEY}" \
      --decrypt --output "${DECRYPTED}" "${ARCHIVE}"
  WORK_ARCHIVE="${DECRYPTED}"
  echo "  ✓ 解密完成"
fi

# -------------------- 解压备份 --------------------
TEMP_DIR=$(mktemp -d)
trap "rm -rf ${TEMP_DIR} ${DECRYPTED:-}" EXIT

echo "[$([ "$NEED_DECRYPT" = true ] && echo 2 || echo 1)/5] 解压备份..."
tar xzf "${WORK_ARCHIVE}" -C "${TEMP_DIR}"
# 过滤出 tar 解压后的目录（排除可能存在的临时解密文件）
BACKUP_SUBDIR=$(find "${TEMP_DIR}" -maxdepth 1 -type d ! -name "$(basename "${TEMP_DIR}")" | head -n 1 | xargs -r basename)
if [ -z "${BACKUP_SUBDIR}" ]; then
  echo "错误：备份文件格式异常"
  exit 1
fi
EXTRACTED="${TEMP_DIR}/${BACKUP_SUBDIR}"

# -------------------- 内部校验和验证 --------------------
echo "[$([ "$NEED_DECRYPT" = true ] && echo 3 || echo 2)/5] 验证内部校验和..."
if [ -f "${EXTRACTED}/checksums.sha256" ]; then
  cd "${EXTRACTED}"
  if ! sha256sum -c checksums.sha256 --quiet 2>/dev/null; then
    echo "错误：内部校验和验证失败，备份内容可能已损坏"
    exit 1
  fi
  echo "  ✓ 内部校验和验证通过"
  cd - > /dev/null
else
  echo "  - 备份中无内部校验和文件，跳过验证"
fi

# Decide the database payload before making a pre-restore backup or changing live files.
SQLITE_ARCHIVE_DB=""
if [ -f "${EXTRACTED}/autoteams.db" ] && [ -f "${EXTRACTED}/autofde.db" ]; then
  echo '错误：归档同时含新旧 SQLite 数据库，拒绝恢复' >&2
  exit 1
fi
if [ -f "${EXTRACTED}/autoteams.db" ]; then
  SQLITE_ARCHIVE_DB="${EXTRACTED}/autoteams.db"
elif [ -f "${EXTRACTED}/autofde.db" ]; then
  SQLITE_ARCHIVE_DB="${EXTRACTED}/autofde.db"
fi
if [ -n "${SQLITE_ARCHIVE_DB}" ] && [ -f "${EXTRACTED}/postgres_dump.sql.gz" ]; then
  echo '错误：归档同时含 PostgreSQL 与 SQLite 数据库，拒绝恢复' >&2
  exit 1
fi

# -------------------- 恢复前自动备份当前数据 --------------------
echo "[$([ "$NEED_DECRYPT" = true ] && echo 4 || echo 3)/5] 恢复前备份当前数据..."
PRE_RESTORE_BACKUP="${BACKUP_DIR}/pre_restore_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${PRE_RESTORE_BACKUP}"
if [ -f "${SQLITE_DB_PATH}" ]; then
  cp "${SQLITE_DB_PATH}" "${PRE_RESTORE_BACKUP}/$(basename "${SQLITE_DB_PATH}")"
  echo "  ✓ 当前 SQLite 已备份到 ${PRE_RESTORE_BACKUP}"
fi
if [ -d "${UPLOADS_DIR}" ]; then
  cp -r "${UPLOADS_DIR}" "${PRE_RESTORE_BACKUP}/uploads"
  echo "  ✓ 当前上传文件已备份到 ${PRE_RESTORE_BACKUP}"
fi
if [ -d "${CHROMA_PERSIST_DIR}" ]; then
  cp -r "${CHROMA_PERSIST_DIR}" "${PRE_RESTORE_BACKUP}/chroma"
  echo "  ✓ 当前 ChromaDB 数据已备份到 ${PRE_RESTORE_BACKUP}"
fi

# -------------------- 确认提示 --------------------
if [ "${SKIP_CONFIRM}" = false ]; then
  echo ""
  echo "============================================================"
  echo "⚠️  警告：即将覆盖当前数据库和上传文件"
  echo "  恢复源: ${ARCHIVE}"
  echo "  数据库: ${SQLITE_DB_PATH}"
  echo "  上传目录: ${UPLOADS_DIR}"
  echo "  当前数据已备份到: ${PRE_RESTORE_BACKUP}"
  echo "============================================================"
  read -p "确认恢复？(输入 yes 继续): " CONFIRM
  if [ "${CONFIRM}" != "yes" ]; then
    echo "已取消恢复"
    exit 0
  fi
fi

# -------------------- 执行恢复 --------------------
echo "[$([ "$NEED_DECRYPT" = true ] && echo 5 || echo 4)/5] 恢复数据库..."

# 检测数据库类型并恢复
if [ -f "${EXTRACTED}/postgres_dump.sql.gz" ]; then
  # PostgreSQL 恢复
  if ! docker ps --format '{{.Names}}' | grep -q "${POSTGRES_CONTAINER}"; then
    echo "错误：PostgreSQL 容器 '${POSTGRES_CONTAINER}' 未运行，无法恢复"
    exit 1
  fi
  # 恢复前先验证压缩包本身可完整解压：把损坏的 .gz 直接灌进 psql 会留下
  # 一个"部分恢复"的数据库，而 psql 的退出码容易被后续噪音掩盖。
  if ! gzip -t "${EXTRACTED}/postgres_dump.sql.gz" 2>/dev/null; then
    echo "错误：postgres_dump.sql.gz 压缩包已损坏，拒绝恢复"
    exit 1
  fi
  if ! gunzip -c "${EXTRACTED}/postgres_dump.sql.gz" > "${TEMP_DIR}/restore.sql"; then
    echo "错误：无法解压 postgres_dump.sql.gz，拒绝恢复"
    exit 1
  fi
  if ! grep -q "PostgreSQL database dump" "${TEMP_DIR}/restore.sql"; then
    echo "错误：转储内容缺少 'PostgreSQL database dump' 头部，拒绝恢复"
    exit 1
  fi
  echo "  恢复到 PostgreSQL..."
  # 单事务执行：任一 SQL 出错时回滚整次数据库恢复，避免留下部分写入。
  if ! docker exec -i "${POSTGRES_CONTAINER}" \
       psql --single-transaction -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -f - \
       < "${TEMP_DIR}/restore.sql"; then
    echo "错误：psql 恢复失败（数据库事务已回滚；上传文件/向量数据未开始恢复）"
    exit 1
  fi
  rm -f "${TEMP_DIR}/restore.sql"
  echo "  ✓ PostgreSQL 恢复完成"
elif [ -n "${SQLITE_ARCHIVE_DB}" ]; then
  # SQLite 恢复：先验证 magic header，避免把损坏文件覆盖到线上库
  if ! head -c 16 "${SQLITE_ARCHIVE_DB}" | grep -q "SQLite format 3"; then
    echo "错误：备份中的 $(basename "${SQLITE_ARCHIVE_DB}") 不是合法 SQLite 文件（magic header 不匹配）"
    exit 1
  fi
  echo "  恢复到 SQLite..."
  mkdir -p "$(dirname "${SQLITE_DB_PATH}")"
  cp "${SQLITE_ARCHIVE_DB}" "${SQLITE_DB_PATH}"
  echo "  ✓ SQLite 恢复完成: ${SQLITE_DB_PATH}"

else
  echo "错误：备份中未找到数据库文件"
  exit 1
fi

echo "[$([ "$NEED_DECRYPT" = true ] && echo 5 || echo 4)/5] 恢复上传文件..."
if [ -f "${EXTRACTED}/uploads.tar.gz" ]; then
  if ! gzip -t "${EXTRACTED}/uploads.tar.gz" 2>/dev/null; then
    echo "错误：uploads.tar.gz 已损坏，拒绝恢复（保留现有上传文件）"
    exit 1
  fi
  mkdir -p "$(dirname "${UPLOADS_DIR}")"
  if [ -d "${UPLOADS_DIR}" ]; then
    rm -rf "${UPLOADS_DIR}"
  fi
  if ! tar xzf "${EXTRACTED}/uploads.tar.gz" -C "$(dirname "${UPLOADS_DIR}")"; then
    echo "错误：上传文件恢复失败（目录已被清空，请用 pre_restore 备份回滚）"
    exit 1
  fi
  echo "  ✓ 上传文件恢复完成: ${UPLOADS_DIR}"
else
  echo "  - 备份中无上传文件，跳过"
fi

# -------------------- 恢复 ChromaDB 数据 (DB-05) --------------------
echo "恢复 ChromaDB 向量数据..."
if [ -f "${EXTRACTED}/chromadb.tar.gz" ]; then
  # 恢复前验证归档可完整解压（magic header + CRC）
  if ! gzip -t "${EXTRACTED}/chromadb.tar.gz" 2>/dev/null; then
    echo "错误：chromadb.tar.gz 已损坏，拒绝恢复（保留现有向量数据）"
    exit 1
  fi
  # 优先恢复到 Docker 容器卷
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "${CHROMADB_CONTAINER}"; then
    echo "  恢复到 ChromaDB 容器 '${CHROMADB_CONTAINER}'..."
    # 清空现有数据后解压（避免残留旧向量）
    docker exec "${CHROMADB_CONTAINER}" sh -c 'rm -rf /chroma/chroma/*' 2>/dev/null || true
    if ! docker exec -i "${CHROMADB_CONTAINER}" tar xzf - -C /chroma < "${EXTRACTED}/chromadb.tar.gz"; then
      echo "错误：ChromaDB 容器恢复失败（卷内数据已被清空，请用 pre_restore 备份回滚）"
      exit 1
    fi
    echo "  ✓ ChromaDB 容器恢复完成（建议重启容器使数据生效）"
  else
    # 嵌入式模式：解压到本地 CHROMA_PERSIST_DIR
    echo "  恢复到嵌入式目录 '${CHROMA_PERSIST_DIR}'..."
    mkdir -p "$(dirname "${CHROMA_PERSIST_DIR}")"
    if [ -d "${CHROMA_PERSIST_DIR}" ]; then
      rm -rf "${CHROMA_PERSIST_DIR}"
    fi
    if ! tar xzf "${EXTRACTED}/chromadb.tar.gz" -C "$(dirname "${CHROMA_PERSIST_DIR}")"; then
      echo "错误：ChromaDB 嵌入式恢复失败"
      exit 1
    fi
    echo "  ✓ ChromaDB 嵌入式恢复完成: ${CHROMA_PERSIST_DIR}"
  fi
else
  echo "  - 备份中无 ChromaDB 数据，跳过"
fi

# 备份时记录的一致性级别：非 quiesced 的向量数据可能包含半写入的段/索引，
# 恢复方必须知道这份 chroma 归档可不可信。
if [ -f "${EXTRACTED}/manifest.txt" ]; then
  echo "============================================================"
  echo "备份清单（备份时记录的一致性）:"
  sed 's/^/  /' "${EXTRACTED}/manifest.txt"
  if grep -qE 'consistency=hot-copy-uncoordinated' "${EXTRACTED}/manifest.txt"; then
    echo "  ⚠ 该备份的向量/上传数据来自运行中目录的一致性未知拷贝，恢复后请运行向量库完整性检查"
  fi
fi

echo "============================================================"
echo "✓ 恢复完成"
echo "  当前数据的恢复前备份: ${PRE_RESTORE_BACKUP}"
echo "  如需回滚，可用该备份执行恢复"
echo "============================================================"
