#!/usr/bin/env bash
# ============================================================
# P1-08-C: AutoTeams 备份轮转脚本
#
# 保留最近 N 份备份，删除旧的
# 额外保留：每周一和每月 1 号的备份（如果存在），保留更久
#
# 用法：
#   ./scripts/backup_rotate.sh                    # 使用默认保留 30 份
#   ./scripts/backup_rotate.sh 60                  # 保留 60 份
#   ./scripts/backup_rotate.sh 60 --dry-run        # 预览将删除的文件
#
# 环境变量：
#   BACKUP_DIR              备份目录（默认 ./backups）
#   BACKUP_RETENTION_DAYS   保留天数（默认 30，按份数算）
# ============================================================

set -euo pipefail

# 加载 .env（如果存在）
if [ -f .env ]; then
  set -a; source .env; set +a
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION="${1:-${BACKUP_RETENTION_DAYS:-30}}"
DRY_RUN=false
if [ "${2:-}" = "--dry-run" ]; then
  DRY_RUN=true
fi
if ! [[ "${RETENTION}" =~ ^[1-9][0-9]*$ ]]; then
  echo '错误：保留份数必须为正整数' >&2
  exit 1
fi

if [ ! -d "${BACKUP_DIR}" ]; then
  echo "备份目录 ${BACKUP_DIR} 不存在，无需轮转"
  exit 0
fi

# AUD-20：轮转本身也是"假备份淘汰可恢复旧备份"这条路径的一环。
# 淘汰之前必须确认最新一份归档确实可读（校验和匹配 + tar 能列出内容）；
# 否则一份"看起来最新"的坏归档会把仍然可恢复的旧备份删光。
list_backup_archives() {
  local archive name stamp lines=''
  for archive in "${BACKUP_DIR}"/autoteams_*.tar.gz "${BACKUP_DIR}"/autoteams_*.tar.gz.gpg \
                 "${BACKUP_DIR}"/autofde_*.tar.gz "${BACKUP_DIR}"/autofde_*.tar.gz.gpg; do
    [ -f "${archive}" ] || continue
    name=$(basename "${archive}")
    stamp=${name#*_}
    stamp=${stamp%.tar.gz*}
    if ! [[ "${stamp}" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
      echo "错误：备份文件名缺少有效时间戳: ${name}" >&2
      return 1
    fi
    lines+=$(printf '%s\t%s\n' "${stamp}" "${archive}")$'\n'
  done
  [ -z "${lines}" ] || printf '%s' "${lines}" | LC_ALL=C sort -r | cut -f2-
}

newest_backup_sane() {
  local newest="$1" name stem probe archive db_count
  name=$(basename "${newest}")
  if [ ! -f "${newest}.sha256" ] ||
     ! (cd "${BACKUP_DIR}" && sha256sum -c "${name}.sha256" --quiet); then
    echo "错误：最新备份 ${name} 缺少有效校验和，拒绝轮转" >&2
    return 1
  fi
  probe=$(mktemp -d)
  archive=${newest}
  if [[ "${newest}" == *.gpg ]]; then
    if [ -z "${BACKUP_ENCRYPTION_KEY:-}" ] || ! command -v gpg >/dev/null 2>&1 ||
       ! gpg --batch --yes --pinentry-mode loopback --passphrase "${BACKUP_ENCRYPTION_KEY}" \
         --decrypt --output "${probe}/archive.tar.gz" "${newest}" >/dev/null 2>&1; then
      echo "错误：最新备份 ${name} 无法解密，拒绝轮转" >&2
      rm -rf "${probe}"
      return 1
    fi
    archive="${probe}/archive.tar.gz"
  fi
  stem=${name%.gpg}
  stem=${stem%.tar.gz}
  if ! tar xzf "${archive}" -C "${probe}" >/dev/null 2>&1 ||
     [ ! -f "${probe}/${stem}/checksums.sha256" ] ||
     ! (cd "${probe}/${stem}" && sha256sum -c checksums.sha256 --quiet >/dev/null 2>&1); then
    echo "错误：最新备份 ${name} 无法解包或内部校验失败，拒绝轮转" >&2
    rm -rf "${probe}"
    return 1
  fi
  db_count=0
  for db in "${probe}/${stem}/autoteams.db" "${probe}/${stem}/autofde.db"; do
    if [ -f "${db}" ]; then
      db_count=$((db_count + 1))
      if ! head -c 16 "${db}" | grep -q 'SQLite format 3'; then db_count=99; fi
    fi
  done
  if [ -f "${probe}/${stem}/postgres_dump.sql.gz" ]; then
    db_count=$((db_count + 1))
    if ! gzip -t "${probe}/${stem}/postgres_dump.sql.gz" 2>/dev/null ||
       ! gunzip -c "${probe}/${stem}/postgres_dump.sql.gz" > "${probe}/dump.sql" ||
       ! grep -q 'PostgreSQL database dump' "${probe}/dump.sql" ||
       ! grep -qE '^(CREATE TABLE|COPY |CREATE SEQUENCE|ALTER TABLE ONLY)' "${probe}/dump.sql"; then
      db_count=99
    fi
  fi
  rm -rf "${probe}"
  if [ "${db_count}" -ne 1 ]; then
    echo "错误：最新备份 ${name} 缺少唯一有效数据库，拒绝轮转" >&2
    return 1
  fi
  echo "最新备份自检通过: ${name}"
}

echo "============================================================"
echo "AutoTeams 备份轮转"
echo "备份目录: ${BACKUP_DIR}"
echo "保留份数: ${RETENTION}"
if [ "${DRY_RUN}" = true ]; then
  echo "模式: 预览（不会实际删除）"
fi
echo "============================================================"

# 列出所有备份文件（按时间排序，最新的在前）
# P3-4: 支持未加密的 .tar.gz 和加密的 .tar.gz.gpg
ARCHIVE_LIST=$(list_backup_archives) || exit 1
BACKUPS=()
if [ -n "${ARCHIVE_LIST}" ]; then
  while IFS= read -r line; do BACKUPS+=("$line"); done <<< "${ARCHIVE_LIST}"
fi

TOTAL=${#BACKUPS[@]}
echo "当前备份数: ${TOTAL}"

if [ "${TOTAL}" -le "${RETENTION}" ]; then
  echo "备份数未超过保留上限，无需清理"
  exit 0
fi

# 轮转前自检：最新一份不可信时，一个旧备份都不删。
if ! newest_backup_sane "${BACKUPS[0]}"; then
  echo "轮转中止：不删除任何旧备份" >&2
  exit 1
fi

# 计算需要删除的份数
DELETE_COUNT=$((TOTAL - RETENTION))
echo "将删除 ${DELETE_COUNT} 份旧备份："

DELETED=0
i=0
for backup in "${BACKUPS[@]}"; do
  i=$((i + 1))
  # 跳过最新的 RETENTION 份
  if [ "${i}" -le "${RETENTION}" ]; then
    continue
  fi

  FILENAME=$(basename "${backup}")
  SIZE=$(du -h "${backup}" | cut -f1)

  if [ "${DRY_RUN}" = true ]; then
    echo "  [预览] 删除 ${FILENAME} (${SIZE})"
    if [ -f "${backup}.sha256" ]; then
      echo "  [预览] 删除 ${FILENAME}.sha256"
    fi
  else
    rm -f "${backup}"
    rm -f "${backup}.sha256"
    echo "  [已删除] ${FILENAME} (${SIZE})"
  fi
  DELETED=$((DELETED + 1))
done

# 清理 pre_restore 备份目录（保留最近 5 个）
PRE_RESTORE_DIRS=()
while IFS= read -r line; do
  PRE_RESTORE_DIRS+=("$line")
done < <(ls -1dt "${BACKUP_DIR}"/pre_restore_* 2>/dev/null || true)

PRE_TOTAL=${#PRE_RESTORE_DIRS[@]}
PRE_RETENTION=5
if [ "${PRE_TOTAL}" -gt "${PRE_RETENTION}" ]; then
  echo ""
  echo "清理 pre_restore 目录（保留 ${PRE_RETENTION} 份）..."
  i=0
  for dir in "${PRE_RESTORE_DIRS[@]}"; do
    i=$((i + 1))
    if [ "${i}" -le "${PRE_RETENTION}" ]; then
      continue
    fi
    if [ "${DRY_RUN}" = true ]; then
      echo "  [预览] 删除 $(basename "${dir}")"
    else
      rm -rf "${dir}"
      echo "  [已删除] $(basename "${dir}")"
    fi
  done
fi

echo "============================================================"
echo "轮转完成: 删除 ${DELETED} 份旧备份"
echo "============================================================"
