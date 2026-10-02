#!/bin/sh
# ============================================================
# AutoTeams 备份容器脚本（在 backup 容器内运行）
#
# 与 scripts/backup_db.sh（宿主机脚本）不同，本脚本在容器内通过
# 网络直接连接 PostgreSQL 服务（PGHOST=postgres），无需 docker exec。
#
# 备份内容：
#   - PostgreSQL 数据库（pg_dump 先落盘、显式检查退出状态，再 gzip）
#   - uploads 上传文件卷（挂载到 ${BACKUP_SOURCE_DIR}/uploads）
#   - chroma 向量数据卷（挂载到 ${BACKUP_SOURCE_DIR}/chroma）
#   - 内部校验和 → 归档 → 恢复自检 → 可选 GPG(AES256) → 成功标记 → 轮转
#
# 失败语义（AUD-20）：
#   1. `pg_dump | gzip` 只取最后一个命令的退出码：gzip 收到空输入照样成功，
#      于是产生"有 gzip 头、有 sha256、但没有数据库"的假备份。
#      本脚本改为 pg_dump 写普通文件 → 检查退出码 → 校验内容 → 再 gzip。
#   2. 转储必须通过 verify_pg_dump（头部标记 + 表/数据定义）才算成功。
#   3. 归档后做一次恢复自检：解包 → 校验内部 checksums.sha256 → 校验数据库文件。
#      自检不通过：删除坏归档，不写最终 .sha256 标记，不轮转，退出非零。
#   4. 轮转是最后一步，且只在整次备份成功后执行。
#
# 一致性说明（诚实声明）：
#   /source/chroma 与 /source/uploads 是运行中的卷，本容器以 :ro 挂载。
#   tar 取到的是"某一瞬间的文件视图"，不是卷快照，可能包含半写入的段/索引。
#   真正的卷快照需要宿主机权限（docker volume / LVM / ZFS 快照），容器内拿不到，
#   因此本脚本不声称提供向量数据的一致性。
#   默认行为：显式告警，并把实际达到的一致性级别写进归档内的 manifest.txt。
#   需要真一致时：在宿主机侧配置 BACKUP_SOURCE_QUIESCE_CMD /
#   BACKUP_SOURCE_RESUME_CMD，或把本容器换成宿主机上的卷快照任务。
#   设置 BACKUP_REQUIRE_SOURCE_QUIESCE=true 可让"没有 quiesce 钩子"直接失败。
#
# 环境变量：
#   POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB  数据库连接
#   PGHOST=postgres（由 compose 注入）
#   BACKUP_DIR             备份输出目录（默认 /backups）
#   BACKUP_SOURCE_DIR      上传/向量卷挂载点（默认 /source）
#   BACKUP_INTERVAL_HOURS  备份间隔小时数（默认 24）
#   BACKUP_RETENTION_DAYS  保留份数（默认 30）
#   BACKUP_ENCRYPTION_KEY  GPG 对称加密密码（为空则不加密）
#   BACKUP_RUN_ONCE        true 时只跑一轮后退出（默认 false，用于自检/CI）
#   PG_WAIT_TIMEOUT        等待 PostgreSQL 就绪的最长秒数（默认 300）
#   BACKUP_SOURCE_QUIESCE_CMD  归档前执行的停写命令（默认空 = 不 quiesce）
#   BACKUP_SOURCE_RESUME_CMD   归档后执行的恢复写入命令（默认空）
#   BACKUP_REQUIRE_SOURCE_QUIESCE  true 时缺少 quiesce 钩子即失败（默认 false）
# ============================================================
set -eu

# pipefail 不是 POSIX（dash 不支持，ash/busybox 与 bash 支持）。
# 显式启用脚本内其余管道：任何一个环节失败都立刻暴露，而不是只看最后一个命令。
if (set -o pipefail) 2>/dev/null; then
  set -o pipefail
fi

BACKUP_DIR="${BACKUP_DIR:-/backups}"
SOURCE_DIR="${BACKUP_SOURCE_DIR:-/source}"
INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
RETENTION="${BACKUP_RETENTION_DAYS:-30}"
ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"
RUN_ONCE="${BACKUP_RUN_ONCE:-false}"
PG_WAIT_TIMEOUT="${PG_WAIT_TIMEOUT:-300}"
SOURCE_QUIESCE_CMD="${BACKUP_SOURCE_QUIESCE_CMD:-}"
SOURCE_RESUME_CMD="${BACKUP_SOURCE_RESUME_CMD:-}"
REQUIRE_QUIESCE="${BACKUP_REQUIRE_SOURCE_QUIESCE:-false}"
if ! printf '%s\n' "${RETENTION}" | grep -Eq '^[1-9][0-9]*$'; then
  echo '错误：保留份数必须为正整数' >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"

err() {
  echo "错误: $*" >&2
}

# 失败时清理半成品工作目录，绝不把失败的中间态留成"看起来像备份"的东西。
BACKUP_WORK_DIR=""
BACKUP_DONE=false
on_exit() {
  _rc=$?
  if [ "${BACKUP_DONE}" != true ]; then
    [ -z "${BACKUP_WORK_DIR}" ] || rm -rf "${BACKUP_WORK_DIR}"
    if [ "${_rc}" -eq 0 ]; then
      _rc=1
    fi
  fi
  exit "${_rc}"
}
trap on_exit EXIT

# 等待 PostgreSQL 就绪；超时失败，避免在坏凭据上无限空转。
wait_for_postgres() {
  echo "等待 PostgreSQL 就绪..."
  _waited=0
  until pg_isready -h "${PGHOST:-postgres}" -U "${POSTGRES_USER:-autoteams}" >/dev/null 2>&1; do
    if [ "${_waited}" -ge "${PG_WAIT_TIMEOUT}" ]; then
      err "等待 PostgreSQL 就绪超时（${PG_WAIT_TIMEOUT}s）"
      return 1
    fi
    sleep 2
    _waited=$((_waited + 2))
  done
  echo "PostgreSQL 已就绪"
}

# 校验 pg_dump 产物确实是一份可恢复的转储。
# 有效 gzip + 有效 sha256 并不能证明里面有数据库。
verify_pg_dump() {
  _file=$1
  if [ ! -s "${_file}" ]; then
    err "pg_dump 产物为空: ${_file}"
    return 1
  fi
  if ! grep -q "PostgreSQL database dump" "${_file}"; then
    err "pg_dump 产物缺少 'PostgreSQL database dump' 头部标记: ${_file}"
    return 1
  fi
  if ! grep -qE "^(CREATE TABLE|COPY |CREATE SEQUENCE|ALTER TABLE ONLY)" "${_file}"; then
    err "pg_dump 产物中没有表/数据定义，疑似不完整: ${_file}"
    return 1
  fi
  return 0
}

# 校验 gzip 容器（magic header + CRC 完整性）。
verify_gzip() {
  _file=$1
  if [ ! -s "${_file}" ]; then
    err "gzip 产物为空: ${_file}"
    return 1
  fi
  if ! gzip -t "${_file}" 2>/dev/null; then
    err "gzip 完整性校验失败: ${_file}"
    return 1
  fi
  return 0
}

# 校验 SQLite 备份的 magic header（"SQLite format 3\0"）。
verify_sqlite() {
  _file=$1
  if [ ! -s "${_file}" ]; then
    err "SQLite 备份为空: ${_file}"
    return 1
  fi
  if ! head -c 16 "${_file}" | grep -q "SQLite format 3"; then
    err "SQLite 备份 magic header 不匹配: ${_file}"
    return 1
  fi
  return 0
}

# 停写钩子。返回 0 表示已真正 quiesce。
quiesce_source() {
  if [ -n "${SOURCE_QUIESCE_CMD}" ]; then
    echo "    quiesce: ${SOURCE_QUIESCE_CMD}"
    if ! sh -c "${SOURCE_QUIESCE_CMD}"; then
      err "BACKUP_SOURCE_QUIESCE_CMD 执行失败"
      return 1
    fi
    return 0
  fi
  if [ "${REQUIRE_QUIESCE}" = true ]; then
    err "BACKUP_REQUIRE_SOURCE_QUIESCE=true 但未配置 BACKUP_SOURCE_QUIESCE_CMD"
    return 1
  fi
  return 2
}

resume_source() {
  if [ -n "${SOURCE_RESUME_CMD}" ]; then
    echo "    resume: ${SOURCE_RESUME_CMD}"
    if ! sh -c "${SOURCE_RESUME_CMD}"; then
      err "BACKUP_SOURCE_RESUME_CMD 执行失败（写入可能仍处于暂停状态）"
      return 1
    fi
  fi
  return 0
}

# 恢复自检：解开刚生成的归档，验证内部校验和与数据库文件。
# 只有"这份备份确实能取出来并还原成数据库"才算一次成功的备份。
verify_archive_restorable() {
  _archive=$1
  _name=$2
  _probe=$(mktemp -d)

  echo "    恢复自检: 解包 $(basename "${_archive}")"
  if ! tar xzf "${_archive}" -C "${_probe}"; then
    err "恢复自检失败: 归档无法解包"
    rm -rf "${_probe}"
    return 1
  fi

  if [ ! -f "${_probe}/${_name}/checksums.sha256" ]; then
    err "恢复自检失败: 归档内缺少 checksums.sha256"
    rm -rf "${_probe}"
    return 1
  fi
  if ! (cd "${_probe}/${_name}" && sha256sum -c checksums.sha256 --quiet); then
    err "恢复自检失败: 内部校验和不匹配"
    rm -rf "${_probe}"
    return 1
  fi

  if [ -f "${_probe}/${_name}/postgres_dump.sql.gz" ]; then
    if ! verify_gzip "${_probe}/${_name}/postgres_dump.sql.gz"; then
      rm -rf "${_probe}"
      return 1
    fi
    if ! gunzip -c "${_probe}/${_name}/postgres_dump.sql.gz" > "${_probe}/dump.sql"; then
      err "恢复自检失败: 归档内数据库无法解压"
      rm -rf "${_probe}"
      return 1
    fi
    if ! verify_pg_dump "${_probe}/dump.sql"; then
      rm -rf "${_probe}"
      return 1
    fi
  elif [ -f "${_probe}/${_name}/autoteams.db" ]; then
    if ! verify_sqlite "${_probe}/${_name}/autoteams.db"; then
      rm -rf "${_probe}"
      return 1
    fi
  else
    err "恢复自检失败: 归档内既没有 postgres_dump.sql.gz 也没有 autoteams.db"
    rm -rf "${_probe}"
    return 1
  fi

  rm -rf "${_probe}"
  echo "    恢复自检通过"
  return 0
}

# 执行一次备份。任一步失败 → set -e 中止 → EXIT trap 清理工作目录 → 退出非零；
# 成功标记与轮转都在最后两步，失败路径永远走不到。
do_backup() {
  _ts=$(date +"%Y%m%d_%H%M%S")
  _work="${BACKUP_DIR}/autoteams_${_ts}"
  mkdir -p "${_work}"
  BACKUP_WORK_DIR="${_work}"

  echo "========================"
  echo "AutoTeams 备份开始 - ${_ts}"
  echo "========================"

  _consistency="not-captured"

  # [1/5] PostgreSQL 备份
  echo "[1/5] 备份 PostgreSQL..."
  _dump_raw="${_work}/postgres_dump.sql"
  if ! PGPASSWORD="${POSTGRES_PASSWORD:-}" \
       pg_dump -h "${PGHOST:-postgres}" -U "${POSTGRES_USER:-autoteams}" \
         -d "${POSTGRES_DB:-autoteams}" --no-owner --no-privileges \
         > "${_dump_raw}"; then
    err "pg_dump 失败（凭据 / 连接 / 磁盘任一原因）"
    return 1
  fi
  if ! verify_pg_dump "${_dump_raw}"; then
    return 1
  fi
  if ! gzip -n -c "${_dump_raw}" > "${_work}/postgres_dump.sql.gz"; then
    err "gzip 压缩失败"
    return 1
  fi
  if ! verify_gzip "${_work}/postgres_dump.sql.gz"; then
    return 1
  fi
  rm -f "${_dump_raw}"
  echo "  ✓ postgres_dump.sql.gz ($(du -h "${_work}/postgres_dump.sql.gz" | cut -f1))"

  # [2/5] 一致性协调
  echo "[2/5] 一致性协调..."
  if [ -d "${SOURCE_DIR}/chroma" ] || [ -d "${SOURCE_DIR}/uploads" ]; then
    if quiesce_source; then
      _consistency="quiesced"
    else
      _quiesce_rc=$?
      if [ "${_quiesce_rc}" -ne 2 ]; then
        return 1
      fi
      _consistency="hot-copy-uncoordinated"
      echo "  ⚠ 未配置停写钩子：uploads/chroma 为运行中目录的非一致性拷贝"
    fi
  fi
  echo "[3/5] 备份上传文件与 ChromaDB..."
  if [ -d "${SOURCE_DIR}/uploads" ]; then
    if ! tar czf "${_work}/uploads.tar.gz" -C "${SOURCE_DIR}" uploads; then
      err "uploads 归档失败"
      resume_source || true
      return 1
    fi
    echo "  ✓ uploads.tar.gz"
  else
    echo "  - 无 ${SOURCE_DIR}/uploads，跳过"
  fi
  if [ -d "${SOURCE_DIR}/chroma" ]; then
    if ! tar czf "${_work}/chromadb.tar.gz" -C "${SOURCE_DIR}" chroma; then
      err "chromadb 归档失败"
      resume_source || true
      return 1
    fi
    echo "  ✓ chromadb.tar.gz"
  else
    echo "  - 无 ${SOURCE_DIR}/chroma，跳过"
  fi
  if ! resume_source; then
    return 1
  fi

  # 记录本次实际达到的一致性级别，恢复时可据此判断向量数据可信度。
  cat > "${_work}/manifest.txt" <<EOF
backup_timestamp=${_ts}
postgres_host=${PGHOST:-postgres}
postgres_db=${POSTGRES_DB:-autoteams}
source_dir=${SOURCE_DIR}
source_consistency=${_consistency}
source_quiesce_cmd=${SOURCE_QUIESCE_CMD:-<none>}
EOF

  # [4/5] 内部校验和 + 打包 + 恢复自检
  echo "[4/5] 生成校验和并打包..."
  (cd "${_work}" && find . -type f ! -name "checksums.sha256" -exec sha256sum {} \; > checksums.sha256)

  _archive="${BACKUP_DIR}/autoteams_${_ts}.tar.gz"
  if ! tar czf "${_archive}" -C "${BACKUP_DIR}" "autoteams_${_ts}"; then
    err "打包归档失败"
    rm -f "${_archive}"
    return 1
  fi
  if ! verify_archive_restorable "${_archive}" "autoteams_${_ts}"; then
    err "归档未通过恢复自检，丢弃该归档（不写成功标记、不轮转）"
    rm -f "${_archive}"
    return 1
  fi
  rm -rf "${_work}"
  BACKUP_WORK_DIR=""
  echo "  ✓ ${_archive}"

  # [5/5] 加密（可选）→ 成功标记 → 轮转
  _final="${_archive}"
  if [ -n "${ENCRYPTION_KEY}" ]; then
    if ! command -v gpg >/dev/null 2>&1; then
      err "已配置 BACKUP_ENCRYPTION_KEY 但容器内未安装 gpg"
      rm -f "${_archive}"
      return 1
    fi
    _enc="${_archive}.gpg"
    echo "加密归档..."
    if ! gpg --batch --yes --pinentry-mode loopback --passphrase "${ENCRYPTION_KEY}" \
            --symmetric --cipher-algo AES256 --compress-algo 0 \
            --output "${_enc}" "${_archive}"; then
      err "GPG 加密失败"
      rm -f "${_enc}" "${_archive}"
      return 1
    fi
    rm -f "${_archive}"
    _final="${_enc}"
  fi

  # 成功标记只在这里写：此时归档已通过恢复自检。
  (cd "${BACKUP_DIR}" && sha256sum "$(basename "${_final}")" > "$(basename "${_final}").sha256")
  echo "  ✓ 成功标记: ${_final}.sha256"

  BACKUP_DONE=true

  # 轮转是最后一步；轮转自身失败不撤销已经成功的备份，但必须显式告警。
  if ! rotate "${RETENTION}"; then
    err "本轮备份已成功，但轮转被跳过（旧备份未被删除）"
  fi
  return 0
}

# 轮转：保留最近 N 份备份，删除旧备份及其校验和
rotate() {
  keep=$1
  archives=$(list_backup_archives) || return 1
  count=$(printf '%s\n' "${archives}" | grep -c . || true)
  echo "当前备份数: ${count}，保留 ${keep} 份"
  if [ -z "${archives}" ] || [ "${count}" -le "${keep}" ]; then
    return 0
  fi

  # 淘汰前先确认最新一份仍可读：最新一份坏掉却继续删旧的，
  # 正是 AUD-20 里"假备份把原本可恢复的旧备份轮转掉"的那条路径。
  newest=$(printf '%s\n' "${archives}" | head -n 1)
  if ! newest_backup_readable "${newest}"; then
    err "最新备份未通过自检，跳过本轮轮转（不删除任何旧备份）"
    return 1
  fi

  n=0
  printf '%s\n' "${archives}" | while IFS= read -r f; do
    n=$((n + 1))
    if [ "${n}" -le "${keep}" ]; then
      continue
    fi
    echo "  删除旧备份: $(basename "${f}")"
    rm -f "${f}" "${f}.sha256"
  done
  return 0
}

list_backup_archives() {
  lines=""
  for archive in "${BACKUP_DIR}"/autoteams_*.tar.gz "${BACKUP_DIR}"/autoteams_*.tar.gz.gpg \
                 "${BACKUP_DIR}"/autofde_*.tar.gz "${BACKUP_DIR}"/autofde_*.tar.gz.gpg; do
    [ -f "${archive}" ] || continue
    name=$(basename "${archive}")
    stamp=${name#*_}
    stamp=${stamp%.tar.gz*}
    if ! printf '%s\n' "${stamp}" | grep -Eq '^[0-9]{8}_[0-9]{6}$'; then
      err "备份文件名缺少有效时间戳: ${name}"
      return 1
    fi
    lines="${lines}${stamp}	${archive}
"
  done
  [ -z "${lines}" ] || printf '%s' "${lines}" | LC_ALL=C sort -r | cut -f2-
}

# 轮转前置检查：最新一份归档必须有有效校验和、内部校验和与唯一数据库。
newest_backup_readable() {
  newest=$1
  name=$(basename "${newest}")
  if [ ! -f "${newest}.sha256" ] ||
     ! (cd "${BACKUP_DIR}" && sha256sum -c "${name}.sha256" --quiet); then
    err "最新备份 ${name} 缺少有效校验和"
    return 1
  fi
  probe=$(mktemp -d)
  checked_archive=${newest}
  case "${newest}" in
    *.gpg)
      if [ -z "${ENCRYPTION_KEY}" ] || ! command -v gpg >/dev/null 2>&1 ||
         ! gpg --batch --yes --pinentry-mode loopback --passphrase "${ENCRYPTION_KEY}" \
           --decrypt --output "${probe}/archive.tar.gz" "${newest}" >/dev/null 2>&1; then
        err "最新备份 ${name} 无法解密"
        rm -rf "${probe}"
        return 1
      fi
      checked_archive="${probe}/archive.tar.gz" ;;
  esac
  stem=${name%.gpg}
  stem=${stem%.tar.gz}
  if ! tar xzf "${checked_archive}" -C "${probe}" >/dev/null 2>&1 ||
     [ ! -f "${probe}/${stem}/checksums.sha256" ] ||
     ! (cd "${probe}/${stem}" && sha256sum -c checksums.sha256 --quiet >/dev/null 2>&1); then
    err "最新备份 ${name} 无法解包或内部校验失败"
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
    err "最新备份 ${name} 缺少唯一有效数据库"
    return 1
  fi
  return 0
}

wait_for_postgres

echo "备份容器启动，间隔 ${INTERVAL_HOURS} 小时执行一次"
while true; do
  do_backup
  if [ "${RUN_ONCE}" = true ]; then
    echo "BACKUP_RUN_ONCE=true，单轮执行完成"
    break
  fi
  echo "休眠 ${INTERVAL_HOURS} 小时..."
  sleep "$((INTERVAL_HOURS * 3600))"
done
