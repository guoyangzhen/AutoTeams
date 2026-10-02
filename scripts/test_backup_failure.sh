#!/usr/bin/env bash
# ============================================================
# AUD-20 回归测试：备份失败必须"响亮地失败"，且绝不轮转旧备份
#
# 覆盖的场景：
#   1. pg_dump 失败（坏凭据）→ backup_db.sh 非零退出
#   2. pg_dump 失败 → 不产生新的归档、不产生 .sha256 成功标记
#   3. pg_dump 失败 → 已有的旧备份一份都不被删除（不轮转）
#   4. pg_dump "成功"但产出空转储（正是 AUD-20 的假备份形态）→ 仍然失败
#   5. backup_rotate.sh 在最新备份不可信时拒绝删除任何旧备份
#   6. backup_container.sh（容器内脚本）同样：pg_dump 失败 → 非零退出 + 不轮转
#
# 全程只使用 mktemp 临时目录 + PATH 注入的假 pg_dump/docker，
# 不接触任何真实备份、真实数据库或真实容器。
#
# 用法：./scripts/test_backup_failure.sh
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DB="${SCRIPT_DIR}/backup_db.sh"
BACKUP_ROTATE="${SCRIPT_DIR}/backup_rotate.sh"
BACKUP_CONTAINER="${SCRIPT_DIR}/backup_container.sh"
RESTORE_DB="${SCRIPT_DIR}/restore_db.sh"

PASS=0
FAIL=0
WORK_ROOT=""

cleanup() {
  [ -z "${WORK_ROOT}" ] || rm -rf "${WORK_ROOT}"
}
trap cleanup EXIT

# 受测脚本会 `source ./.env`（相对 CWD）。每个用例都在自己的临时目录里以
# env -i 运行，PATH 固定为系统工具目录，绝不把仓库目录或开发者本机配置
# 带进被测进程——否则测试结果会取决于本机有没有 .env。
BASH_BIN="$(command -v bash)"
SAFE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"


ok() {
  PASS=$((PASS + 1))
  echo "  PASS: $1"
}

ko() {
  FAIL=$((FAIL + 1))
  echo "  FAIL: $1"
}

assert_nonzero() {
  if [ "$2" -ne 0 ]; then
    ok "$1 (exit=$2)"
  else
    ko "$1 (exit=0 —— 失败被吞掉了)"
  fi
}

assert_zero() {
  if [ "$2" -eq 0 ]; then
    ok "$1 (exit=0)"
  else
    ko "$1 (exit=$2)"
  fi
}

# 造一份"健康"的旧备份归档：带有效 pg_dump 头 + 有效 .sha256 标记。
make_healthy_backup() {
  local dir="$1" name="$2"
  local work="${dir}/${name}"
  mkdir -p "${work}"
  cat > "${work}/postgres_dump.sql" <<'SQL'
--
-- PostgreSQL database dump
--
SET statement_timeout = 0;
CREATE TABLE public.widgets (
    id integer NOT NULL,
    label text
);
COPY public.widgets (id, label) FROM stdin;
1	alpha
\.
ALTER TABLE ONLY public.widgets ADD CONSTRAINT widgets_pkey PRIMARY KEY (id);
SQL
  gzip -n -c "${work}/postgres_dump.sql" > "${work}/postgres_dump.sql.gz"
  rm -f "${work}/postgres_dump.sql"
  printf 'source_consistency=quiesced\n' > "${work}/manifest.txt"
  (cd "${work}" && find . -type f ! -name "checksums.sha256" -exec sha256sum {} \; > checksums.sha256)
  tar czf "${dir}/${name}.tar.gz" -C "${dir}" "${name}"
  rm -rf "${work}"
  (cd "${dir}" && sha256sum "${name}.tar.gz" > "${name}.tar.gz.sha256")
}

# 注入假的 docker / pg_dump / pg_isready，用 PATH 优先级覆盖真实工具。
make_fake_bin() {
  local bin="$1"
  mkdir -p "${bin}"

  cat > "${bin}/docker" <<'FAKE'
#!/usr/bin/env bash
# 假 docker：容器"在运行"，但 pg_dump 因为坏凭据失败。
case "$1" in
  ps)
    echo "autoteams-postgres-1"
    exit 0
    ;;
  exec)
    shift
    # docker exec <container> pg_dump ...
    if [ "$1" = "autoteams-postgres-1" ]; then shift; fi
    if [ "$1" = "pg_dump" ]; then
      echo "pg_dump: error: connection to server at \"postgres\" (172.18.0.2), port 5432 failed: FATAL:  password authentication failed for user \"autoteams\"" >&2
      exit 1
    fi
    exit 0
    ;;
esac
exit 0
FAKE

  cat > "${bin}/pg_dump" <<'FAKE'
#!/usr/bin/env bash
# 假 pg_dump：模拟坏凭据导致的连接失败（输出到 stdout 为空）
echo "pg_dump: error: FATAL:  password authentication failed" >&2
exit 1
FAKE

  cat > "${bin}/pg_isready" <<'FAKE'
#!/usr/bin/env bash
exit 0
FAKE

  chmod +x "${bin}/docker" "${bin}/pg_dump" "${bin}/pg_isready"
}

WORK_ROOT="$(mktemp -d)"
echo "临时工作目录: ${WORK_ROOT}"
echo

# ---------------------------------------------------------------
echo "[1] backup_db.sh：pg_dump 失败（坏凭据）必须非零退出且不轮转"
# ---------------------------------------------------------------
BD_DIR="${WORK_ROOT}/case1"
FAKE_BIN="${WORK_ROOT}/bin1"
mkdir -p "${BD_DIR}"
make_fake_bin "${FAKE_BIN}"
# 三份健康旧备份；保留上限 1 意味着"如果脚本继续跑下去"就会删掉 2 份
make_healthy_backup "${BD_DIR}" "autoteams_20260101_000000"
make_healthy_backup "${BD_DIR}" "autoteams_20260102_000000"
make_healthy_backup "${BD_DIR}" "autoteams_20260103_000000"
BEFORE_COUNT=$(ls -1 "${BD_DIR}"/autoteams_*.tar.gz | wc -l | tr -d ' ')

set +e
(
  cd "${BD_DIR}" || exit 1
  env -i \
    PATH="${FAKE_BIN}:${SAFE_PATH}" \
    HOME="${BD_DIR}" \
    BACKUP_DIR="${BD_DIR}" \
    BACKUP_RETENTION_DAYS=1 \
    BACKUP_POSTGRES_CONTAINER=autoteams-postgres-1 \
    UPLOADS_DIR="${BD_DIR}/no-uploads" \
    CHROMA_PERSIST_DIR="${BD_DIR}/no-chroma" \
    "${BASH_BIN}" "${BACKUP_DB}" --postgres
) > "${WORK_ROOT}/case1.log" 2>&1
BD_RC=$?
set -e

AFTER_COUNT=$(ls -1 "${BD_DIR}"/autoteams_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
NEW_ARCHIVES=$(ls -1 "${BD_DIR}"/autoteams_*.tar.gz 2>/dev/null | grep -vc -E 'autoteams_2026010[123]_' || true)
NEW_MARKERS=$(ls -1 "${BD_DIR}"/autoteams_*.tar.gz.sha256 2>/dev/null | grep -vc -E 'autoteams_2026010[123]_' || true)

assert_nonzero "pg_dump 失败时脚本非零退出" "${BD_RC}"
if [ "${NEW_ARCHIVES}" -eq 0 ]; then
  ok "未产生新的归档"
else
  ko "产生了 ${NEW_ARCHIVES} 个新归档（应为空）"
fi
if [ "${NEW_MARKERS}" -eq 0 ]; then
  ok "未写入新的 .sha256 成功标记"
else
  ko "写入了 ${NEW_MARKERS} 个新的 .sha256 标记（应为空）"
fi
if [ "${AFTER_COUNT}" -eq "${BEFORE_COUNT}" ]; then
  ok "未轮转：${BEFORE_COUNT} 份旧备份全部保留"
else
  ko "发生了轮转：${BEFORE_COUNT} → ${AFTER_COUNT}"
fi
if [ -z "$(ls -d "${BD_DIR}"/autoteams_2* 2>/dev/null | grep -v 'autoteams_2026010[123]' || true)" ]; then
  ok "未残留半成品工作目录"
else
  ko "残留了半成品工作目录"
fi
echo "--- 脚本输出（末尾 6 行）---"
tail -n 6 "${WORK_ROOT}/case1.log"
echo

# ---------------------------------------------------------------
echo "[2] backup_db.sh：pg_dump '成功'但产出空转储（假备份形态）必须被拒"
# ---------------------------------------------------------------
EMPTY_DIR="${WORK_ROOT}/case2"
FAKE_BIN2="${WORK_ROOT}/bin2"
mkdir -p "${EMPTY_DIR}" "${FAKE_BIN2}"
# 假 docker：容器在运行，pg_dump 退出 0 但什么都不输出 —— 这正是
# "gzip 成功 + 有 sha256 + 没有数据库"的假备份来源。
cat > "${FAKE_BIN2}/docker" <<'FAKE'
#!/usr/bin/env bash
case "$1" in
  ps) echo "autoteams-postgres-1"; exit 0 ;;
  exec)
    shift
    if [ "$1" = "autoteams-postgres-1" ]; then shift; fi
    if [ "$1" = "pg_dump" ]; then
      # 退出码 0，但输出为空
      exit 0
    fi
    exit 0 ;;
esac
exit 0
FAKE
chmod +x "${FAKE_BIN2}/docker"
make_healthy_backup "${EMPTY_DIR}" "autoteams_20260101_000000"
EMPTY_BEFORE=$(ls -1 "${EMPTY_DIR}"/autoteams_*.tar.gz | wc -l | tr -d ' ')

set +e
(
  cd "${EMPTY_DIR}" || exit 1
  env -i \
    PATH="${FAKE_BIN2}:${SAFE_PATH}" \
    HOME="${EMPTY_DIR}" \
    BACKUP_DIR="${EMPTY_DIR}" \
    BACKUP_RETENTION_DAYS=1 \
    BACKUP_POSTGRES_CONTAINER=autoteams-postgres-1 \
    UPLOADS_DIR="${EMPTY_DIR}/no-uploads" \
    CHROMA_PERSIST_DIR="${EMPTY_DIR}/no-chroma" \
    "${BASH_BIN}" "${BACKUP_DB}" --postgres
) > "${WORK_ROOT}/case2.log" 2>&1
EMPTY_RC=$?
set -e

EMPTY_AFTER=$(ls -1 "${EMPTY_DIR}"/autoteams_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
EMPTY_NEW=$(ls -1 "${EMPTY_DIR}"/autoteams_*.tar.gz 2>/dev/null | grep -vc 'autoteams_20260101_000000' || true)

assert_nonzero "空转储被拒绝，非零退出" "${EMPTY_RC}"
if [ "${EMPTY_NEW}" -eq 0 ]; then
  ok "空转储没有变成归档"
else
  ko "空转储被归档成了 ${EMPTY_NEW} 份备份（假备份！）"
fi
if [ "${EMPTY_AFTER}" -eq "${EMPTY_BEFORE}" ]; then
  ok "未轮转：旧备份保留"
else
  ko "发生了轮转"
fi
echo "--- 脚本输出（末尾 4 行）---"
tail -n 4 "${WORK_ROOT}/case2.log"
echo

# ---------------------------------------------------------------
echo "[3] backup_rotate.sh：最新备份不可信时拒绝删除任何旧备份"
# ---------------------------------------------------------------
ROT_DIR="${WORK_ROOT}/case3"
mkdir -p "${ROT_DIR}"
make_healthy_backup "${ROT_DIR}" "autoteams_20260101_000000"
make_healthy_backup "${ROT_DIR}" "autoteams_20260102_000000"
# 最新的这份"备份"只有 gzip 头，里面没有数据库，且校验和文件内容不匹配
printf '\x1f\x8b\x08\x00garbage' > "${ROT_DIR}/autoteams_20260103_000000.tar.gz"
printf 'deadbeef  autoteams_20260103_000000.tar.gz\n' > "${ROT_DIR}/autoteams_20260103_000000.tar.gz.sha256"
ROT_BEFORE=$(ls -1 "${ROT_DIR}"/autoteams_*.tar.gz | wc -l | tr -d ' ')

set +e
# 在隔离的 CWD 里运行：这些脚本会 `source ./.env`，
# 若从仓库根目录运行就会读到开发者真实的 .env，让测试结果依赖本机配置。
(
  cd "${ROT_DIR}" || exit 1
  env -i PATH="${SAFE_PATH}" HOME="${ROT_DIR}" BACKUP_DIR="${ROT_DIR}" \
    "${BASH_BIN}" "${BACKUP_ROTATE}" 1
) > "${WORK_ROOT}/case3.log" 2>&1
ROT_RC=$?
set -e

ROT_AFTER=$(ls -1 "${ROT_DIR}"/autoteams_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
assert_nonzero "最新备份损坏时轮转非零退出" "${ROT_RC}"
if [ "${ROT_AFTER}" -eq "${ROT_BEFORE}" ]; then
  ok "未删除任何旧备份（${ROT_AFTER} 份全部保留）"
else
  ko "轮转删除了旧备份：${ROT_BEFORE} → ${ROT_AFTER}"
fi
echo "--- 脚本输出（末尾 3 行）---"
tail -n 3 "${WORK_ROOT}/case3.log"
echo

# ---------------------------------------------------------------
echo "[4] backup_rotate.sh：最新备份健康时正常轮转（防止把门禁焊死）"
# ---------------------------------------------------------------
ROT_OK="${WORK_ROOT}/case4"
mkdir -p "${ROT_OK}"
make_healthy_backup "${ROT_OK}" "autoteams_20260101_000000"
make_healthy_backup "${ROT_OK}" "autoteams_20260102_000000"
make_healthy_backup "${ROT_OK}" "autoteams_20260103_000000"

set +e
# 在隔离的 CWD 里运行：这些脚本会 `source ./.env`，
# 若从仓库根目录运行就会读到开发者真实的 .env，让测试结果依赖本机配置。
(
  cd "${ROT_OK}" || exit 1
  env -i PATH="${SAFE_PATH}" HOME="${ROT_OK}" BACKUP_DIR="${ROT_OK}" \
    "${BASH_BIN}" "${BACKUP_ROTATE}" 1
) > "${WORK_ROOT}/case4.log" 2>&1
ROT_OK_RC=$?
set -e

ROT_OK_AFTER=$(ls -1 "${ROT_OK}"/autoteams_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
assert_zero "最新备份健康时轮转成功" "${ROT_OK_RC}"
if [ "${ROT_OK_AFTER}" -eq 1 ]; then
  ok "健康时正常轮转到 1 份"
else
  ko "健康时未轮转（剩 ${ROT_OK_AFTER} 份）"
fi
echo

# ---------------------------------------------------------------
echo "[5] backup_container.sh：pg_dump 失败必须非零退出且不轮转"
# ---------------------------------------------------------------
BC_DIR="${WORK_ROOT}/case5"
FAKE_BIN5="${WORK_ROOT}/bin5"
BC_SOURCE="${WORK_ROOT}/case5-source"
mkdir -p "${BC_DIR}" "${BC_SOURCE}/uploads" "${BC_SOURCE}/chroma"
echo "user upload" > "${BC_SOURCE}/uploads/a.txt"
echo "vector segment" > "${BC_SOURCE}/chroma/segment.bin"
make_fake_bin "${FAKE_BIN5}"
make_healthy_backup "${BC_DIR}" "autoteams_20260101_000000"
make_healthy_backup "${BC_DIR}" "autoteams_20260102_000000"
BC_BEFORE=$(ls -1 "${BC_DIR}"/autoteams_*.tar.gz | wc -l | tr -d ' ')

set +e
(
  cd "${BC_DIR}" || exit 1
  env -i \
    PATH="${FAKE_BIN5}:${SAFE_PATH}" \
    HOME="${BC_DIR}" \
    PGHOST=postgres \
    POSTGRES_USER=autoteams \
    POSTGRES_PASSWORD=definitely-wrong-password \
    POSTGRES_DB=autoteams \
    BACKUP_DIR="${BC_DIR}" \
    BACKUP_SOURCE_DIR="${BC_SOURCE}" \
    BACKUP_RETENTION_DAYS=1 \
    BACKUP_RUN_ONCE=true \
    PG_WAIT_TIMEOUT=5 \
    sh "${BACKUP_CONTAINER}"
) > "${WORK_ROOT}/case5.log" 2>&1
BC_RC=$?
set -e

BC_AFTER=$(ls -1 "${BC_DIR}"/autoteams_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
BC_NEW=$(ls -1 "${BC_DIR}"/autoteams_*.tar.gz 2>/dev/null | grep -vc -E 'autoteams_2026010[12]_' || true)
BC_NEW_MARKERS=$(ls -1 "${BC_DIR}"/autoteams_*.tar.gz.sha256 2>/dev/null | grep -vc -E 'autoteams_2026010[12]_' || true)

assert_nonzero "容器脚本在 pg_dump 失败时非零退出" "${BC_RC}"
if [ "${BC_NEW}" -eq 0 ] && [ "${BC_NEW_MARKERS}" -eq 0 ]; then
  ok "未产生新归档、未写成功标记"
else
  ko "产生了新归档=${BC_NEW} 新标记=${BC_NEW_MARKERS}（应都为 0）"
fi
if [ "${BC_AFTER}" -eq "${BC_BEFORE}" ]; then
  ok "未轮转：${BC_BEFORE} 份旧备份全部保留"
else
  ko "发生了轮转：${BC_BEFORE} → ${BC_AFTER}"
fi
echo "--- 脚本输出（末尾 5 行）---"
tail -n 5 "${WORK_ROOT}/case5.log"
echo

# ---------------------------------------------------------------
echo "[6] backup_container.sh：pg_dump 成功时完成备份、写标记并归档真实内容"
# ---------------------------------------------------------------
BC_OK="${WORK_ROOT}/case6"
FAKE_BIN6="${WORK_ROOT}/bin6"
BC_SRC_OK="${WORK_ROOT}/case6-source"
mkdir -p "${BC_OK}" "${BC_SRC_OK}/uploads" "${BC_SRC_OK}/chroma"
echo "user upload" > "${BC_SRC_OK}/uploads/a.txt"
echo "vector segment" > "${BC_SRC_OK}/chroma/segment.bin"
mkdir -p "${FAKE_BIN6}"
# 假 docker 不需要；backup_container.sh 直接调用 pg_dump
cat > "${FAKE_BIN6}/pg_dump" <<'FAKE'
#!/usr/bin/env bash
cat <<'SQL'
--
-- PostgreSQL database dump
--
CREATE TABLE public.widgets (id integer);
COPY public.widgets (id) FROM stdin;
1
\.
SQL
FAKE
cat > "${FAKE_BIN6}/pg_isready" <<'FAKE'
#!/usr/bin/env bash
exit 0
FAKE
chmod +x "${FAKE_BIN6}/pg_dump" "${FAKE_BIN6}/pg_isready"

set +e
(
  cd "${BC_OK}" || exit 1
  env -i \
    PATH="${FAKE_BIN6}:${SAFE_PATH}" \
    HOME="${BC_OK}" \
    PGHOST=postgres \
    POSTGRES_USER=autoteams \
    POSTGRES_PASSWORD=pw \
    POSTGRES_DB=autoteams \
    BACKUP_DIR="${BC_OK}" \
    BACKUP_SOURCE_DIR="${BC_SRC_OK}" \
    BACKUP_RETENTION_DAYS=30 \
    BACKUP_RUN_ONCE=true \
    PG_WAIT_TIMEOUT=5 \
    sh "${BACKUP_CONTAINER}"
) > "${WORK_ROOT}/case6.log" 2>&1
BC_OK_RC=$?
set -e

BC_OK_ARCHIVES=$(ls -1 "${BC_OK}"/autoteams_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
BC_OK_MARKERS=$(ls -1 "${BC_OK}"/autoteams_*.tar.gz.sha256 2>/dev/null | wc -l | tr -d ' ')
assert_zero "成功路径：脚本退出 0" "${BC_OK_RC}"
if [ "${BC_OK_ARCHIVES}" -eq 1 ] && [ "${BC_OK_MARKERS}" -eq 1 ]; then
  ok "成功路径：产生 1 份归档 + 1 个成功标记"
else
  ko "成功路径异常：归档=${BC_OK_ARCHIVES} 标记=${BC_OK_MARKERS}"
fi
if grep -q "恢复自检通过" "${WORK_ROOT}/case6.log"; then
  ok "成功路径：执行了恢复自检"
else
  ko "成功路径：没有看到恢复自检输出"
fi
if tar tzf "${BC_OK}"/autoteams_*.tar.gz 2>/dev/null | grep -q "manifest.txt"; then
  ok "归档内含 manifest.txt（记录一致性级别）"
else
  ko "归档内缺少 manifest.txt"
fi
if tar xzOf "${BC_OK}"/autoteams_*.tar.gz --wildcards '*/manifest.txt' 2>/dev/null | grep -q 'source_consistency='; then
  ok "manifest 记录了 source_consistency"
else
  ko "manifest 未记录 source_consistency"
fi
echo

# ---------------------------------------------------------------
echo "[7] 强制停写时缺少/失败的钩子必须终止备份"
# ---------------------------------------------------------------
for mode in missing failing; do
  STRICT_DIR="${WORK_ROOT}/strict-${mode}"
  mkdir -p "${STRICT_DIR}/source/chroma"
  echo "vector segment" > "${STRICT_DIR}/source/chroma/segment.bin"
  if [ "${mode}" = failing ]; then
    QUIESCE_CMD=false
  else
    QUIESCE_CMD=""
  fi

  set +e
  (
    cd "${STRICT_DIR}" || exit 1
    env -i PATH="${FAKE_BIN6}:${SAFE_PATH}" HOME="${STRICT_DIR}" \
      PGHOST=postgres POSTGRES_USER=autoteams POSTGRES_DB=autoteams \
      BACKUP_DIR="${STRICT_DIR}/container-backups" \
      BACKUP_SOURCE_DIR="${STRICT_DIR}/source" \
      BACKUP_REQUIRE_SOURCE_QUIESCE=true \
      BACKUP_SOURCE_QUIESCE_CMD="${QUIESCE_CMD}" \
      BACKUP_RUN_ONCE=true PG_WAIT_TIMEOUT=5 \
      sh "${BACKUP_CONTAINER}"
  ) > "${STRICT_DIR}/container.log" 2>&1
  STRICT_CONTAINER_RC=$?
  set -e
  assert_nonzero "容器脚本强制停写 ${mode} 时拒绝备份" "${STRICT_CONTAINER_RC}"
  if [ -z "$(find "${STRICT_DIR}/container-backups" -maxdepth 1 -name 'autoteams_*.tar.gz*' -print -quit)" ]; then
    ok "容器脚本强制停写 ${mode} 时无归档或成功标记"
  else
    ko "容器脚本强制停写 ${mode} 后留下归档"
  fi
done

STRICT_HOST="${WORK_ROOT}/strict-host"
STRICT_BIN="${STRICT_HOST}/bin"
mkdir -p "${STRICT_BIN}" "${STRICT_HOST}/chroma"
cat > "${STRICT_BIN}/docker" <<'FAKE'
#!/usr/bin/env bash
case "$1" in
  ps) echo autoteams-postgres-1; exit 0 ;;
  exec)
    shift 2
    if [ "$1" = pg_dump ]; then
      printf '%s\n' '-- PostgreSQL database dump' 'CREATE TABLE public.widgets (id integer);'
      exit 0
    fi ;;
esac
exit 1
FAKE
chmod +x "${STRICT_BIN}/docker"
set +e
(
  cd "${STRICT_HOST}" || exit 1
  env -i PATH="${STRICT_BIN}:${SAFE_PATH}" HOME="${STRICT_HOST}" \
    BACKUP_DIR="${STRICT_HOST}/backups" \
    CHROMA_PERSIST_DIR="${STRICT_HOST}/chroma" \
    UPLOADS_DIR="${STRICT_HOST}/no-uploads" \
    BACKUP_REQUIRE_SOURCE_QUIESCE=true \
    "${BASH_BIN}" "${BACKUP_DB}" --postgres
) > "${STRICT_HOST}/host.log" 2>&1
STRICT_HOST_RC=$?
set -e
assert_nonzero "宿主机脚本强制停写无钩子时拒绝备份" "${STRICT_HOST_RC}"
if [ -z "$(find "${STRICT_HOST}/backups" -maxdepth 1 -name 'autoteams_*.tar.gz*' -print -quit)" ]; then
  ok "宿主机脚本强制停写无钩子时无归档或成功标记"
else
  ko "宿主机脚本强制停写无钩子后留下归档"
fi
echo

# ---------------------------------------------------------------
echo "[8] SQLite 旧路径备份与双路径歧义"
# ---------------------------------------------------------------
SQL_CASE="${WORK_ROOT}/sqlite-compat"
mkdir -p "${SQL_CASE}/backend" "${SQL_CASE}/bin"
printf 'SQLite format 3\000legacy-content\n' > "${SQL_CASE}/backend/autofde.db"
cat > "${SQL_CASE}/bin/docker" <<'FAKE'
#!/usr/bin/env bash
exit 1
FAKE
cat > "${SQL_CASE}/bin/sqlite3" <<'FAKE'
#!/usr/bin/env bash
dest=${2#*.backup \'}
dest=${dest%\'}
cp "$1" "$dest"
FAKE
chmod +x "${SQL_CASE}/bin/docker" "${SQL_CASE}/bin/sqlite3"
set +e
(
  cd "${SQL_CASE}" || exit 1
  env -i PATH="${SQL_CASE}/bin:${SAFE_PATH}" HOME="${SQL_CASE}" \
    BACKUP_DIR="${SQL_CASE}/backups" UPLOADS_DIR="${SQL_CASE}/none" \
    CHROMA_PERSIST_DIR="${SQL_CASE}/none-chroma" \
    "${BASH_BIN}" "${BACKUP_DB}" --sqlite
) > "${SQL_CASE}/backup.log" 2>&1
SQL_RC=$?
set -e
assert_zero '仅旧 SQLite 路径时仍可备份' "${SQL_RC}"
SQL_ARCHIVE=$(find "${SQL_CASE}/backups" -maxdepth 1 -name 'autoteams_*.tar.gz' -print -quit)
if [ -n "${SQL_ARCHIVE}" ] && tar tzf "${SQL_ARCHIVE}" | grep -q '/autoteams.db$'; then
  ok '旧数据库被打入新名称归档及载荷'
else
  ko '新名称归档未包含旧 SQLite 数据'
fi
printf 'SQLite format 3\000new-content\n' > "${SQL_CASE}/backend/autoteams.db"
BEFORE_SQL=$(find "${SQL_CASE}/backups" -maxdepth 1 -name '*.tar.gz' | wc -l)
set +e
(
  cd "${SQL_CASE}" || exit 1
  env -i PATH="${SQL_CASE}/bin:${SAFE_PATH}" HOME="${SQL_CASE}" \
    BACKUP_DIR="${SQL_CASE}/backups" "${BASH_BIN}" "${BACKUP_DB}" --sqlite
) > "${SQL_CASE}/ambiguous.log" 2>&1
AMBIG_RC=$?
set -e
AFTER_SQL=$(find "${SQL_CASE}/backups" -maxdepth 1 -name '*.tar.gz' | wc -l)
assert_nonzero '新旧 SQLite 同时存在时默认备份失败' "${AMBIG_RC}"
if [ "${BEFORE_SQL}" -eq "${AFTER_SQL}" ]; then ok '歧义未产生归档'; else ko '歧义产生了归档'; fi
set +e
(
  cd "${SQL_CASE}" || exit 1
  env -i PATH="${SQL_CASE}/bin:${SAFE_PATH}" HOME="${SQL_CASE}" \
    BACKUP_DIR="${SQL_CASE}/explicit" SQLITE_DB_PATH=./backend/autofde.db \
    UPLOADS_DIR="${SQL_CASE}/none" CHROMA_PERSIST_DIR="${SQL_CASE}/none-chroma" \
    "${BASH_BIN}" "${BACKUP_DB}" --sqlite
) > "${SQL_CASE}/explicit.log" 2>&1
EXPLICIT_RC=$?
set -e
assert_zero '显式 SQLite 路径可解除歧义' "${EXPLICIT_RC}"

# The archive is synthetic and isolated; no live SQLite database is opened.
LEGACY_RESTORE="${WORK_ROOT}/legacy-restore"
mkdir -p "${LEGACY_RESTORE}/autofde_20260104_000000" "${LEGACY_RESTORE}/backend"
printf 'SQLite format 3\000restored-legacy\n' > "${LEGACY_RESTORE}/autofde_20260104_000000/autofde.db"
(cd "${LEGACY_RESTORE}/autofde_20260104_000000" && sha256sum autofde.db > checksums.sha256)
tar czf "${LEGACY_RESTORE}/autofde_20260104_000000.tar.gz" -C "${LEGACY_RESTORE}" autofde_20260104_000000
(cd "${LEGACY_RESTORE}" && sha256sum autofde_20260104_000000.tar.gz > autofde_20260104_000000.tar.gz.sha256)
rm -rf "${LEGACY_RESTORE}/autofde_20260104_000000"
printf 'SQLite format 3\000before-restore\n' > "${LEGACY_RESTORE}/backend/autofde.db"
set +e
(
  cd "${LEGACY_RESTORE}" || exit 1
  env -i PATH="${SQL_CASE}/bin:${SAFE_PATH}" HOME="${LEGACY_RESTORE}" \
    BACKUP_DIR="${LEGACY_RESTORE}" UPLOADS_DIR="${LEGACY_RESTORE}/none" \
    CHROMA_PERSIST_DIR="${LEGACY_RESTORE}/none-chroma" \
    "${BASH_BIN}" "${RESTORE_DB}" "${LEGACY_RESTORE}/autofde_20260104_000000.tar.gz" --yes
) > "${LEGACY_RESTORE}/restore.log" 2>&1
RESTORE_RC=$?
set -e
assert_zero '旧归档内 autofde.db 可恢复' "${RESTORE_RC}"
if grep -q 'restored-legacy' "${LEGACY_RESTORE}/backend/autofde.db" &&
   [ ! -e "${LEGACY_RESTORE}/backend/autoteams.db" ]; then
  ok '恢复沿用旧 SQLite 路径，未新建并行数据库'
else
  ko '恢复未正确沿用旧 SQLite 路径'
fi
printf 'SQLite format 3\000new-live\n' > "${LEGACY_RESTORE}/backend/autoteams.db"
set +e
(
  cd "${LEGACY_RESTORE}" || exit 1
  env -i PATH="${SQL_CASE}/bin:${SAFE_PATH}" HOME="${LEGACY_RESTORE}" \
    BACKUP_DIR="${LEGACY_RESTORE}" "${BASH_BIN}" "${RESTORE_DB}" \
    "${LEGACY_RESTORE}/autofde_20260104_000000.tar.gz" --yes
) > "${LEGACY_RESTORE}/restore-ambiguous.log" 2>&1
RESTORE_AMBIG_RC=$?
set -e
assert_nonzero '新旧 SQLite 同时存在时默认恢复失败' "${RESTORE_AMBIG_RC}"
if grep -q 'new-live' "${LEGACY_RESTORE}/backend/autoteams.db"; then ok '歧义未覆盖新数据库'; else ko '歧义覆盖了新数据库'; fi
# An archive with both SQLite payload names is ambiguous even with one live target.
rm -f "${LEGACY_RESTORE}/backend/autoteams.db"
mkdir -p "${LEGACY_RESTORE}/autofde_20260105_000000"
cp "${LEGACY_RESTORE}/backend/autofde.db" "${LEGACY_RESTORE}/autofde_20260105_000000/autofde.db"
cp "${LEGACY_RESTORE}/backend/autofde.db" "${LEGACY_RESTORE}/autofde_20260105_000000/autoteams.db"
(cd "${LEGACY_RESTORE}/autofde_20260105_000000" && sha256sum autofde.db autoteams.db > checksums.sha256)
tar czf "${LEGACY_RESTORE}/autofde_20260105_000000.tar.gz" -C "${LEGACY_RESTORE}" autofde_20260105_000000
(cd "${LEGACY_RESTORE}" && sha256sum autofde_20260105_000000.tar.gz > autofde_20260105_000000.tar.gz.sha256)
rm -rf "${LEGACY_RESTORE}/autofde_20260105_000000"
set +e
(
  cd "${LEGACY_RESTORE}" || exit 1
  env -i PATH="${SQL_CASE}/bin:${SAFE_PATH}" HOME="${LEGACY_RESTORE}" \
    BACKUP_DIR="${LEGACY_RESTORE}" "${BASH_BIN}" "${RESTORE_DB}" \
    "${LEGACY_RESTORE}/autofde_20260105_000000.tar.gz" --yes
) > "${LEGACY_RESTORE}/dual-payload.log" 2>&1
DUAL_PAYLOAD_RC=$?
set -e
assert_nonzero '归档同时含新旧 SQLite 载荷时拒绝恢复' "${DUAL_PAYLOAD_RC}"
if grep -q 'restored-legacy' "${LEGACY_RESTORE}/backend/autofde.db"; then ok '歧义归档未覆盖旧数据库'; else ko '歧义归档覆盖了旧数据库'; fi
echo

# ---------------------------------------------------------------
echo '[9] 主机轮转：混合品牌按时间戳排序，坏最新备份绝不删旧备份'
# ---------------------------------------------------------------
MIXED="${WORK_ROOT}/mixed-rotation"
mkdir -p "${MIXED}"
make_healthy_backup "${MIXED}" autofde_20260101_000000
make_healthy_backup "${MIXED}" autoteams_20260102_000000
printf 'bad' > "${MIXED}/autofde_20260103_000000.tar.gz"
(cd "${MIXED}" && sha256sum autofde_20260103_000000.tar.gz > autofde_20260103_000000.tar.gz.sha256)
set +e
(
  cd "${MIXED}" || exit 1
  env -i PATH="${SAFE_PATH}" HOME="${MIXED}" BACKUP_DIR="${MIXED}" \
    "${BASH_BIN}" "${BACKUP_ROTATE}" 2
) > "${MIXED}/bad.log" 2>&1
MIXED_BAD_RC=$?
set -e
assert_nonzero '最新旧品牌归档损坏时拒绝轮转' "${MIXED_BAD_RC}"
if [ "$(find "${MIXED}" -maxdepth 1 -name '*.tar.gz' | wc -l)" -eq 3 ] &&
   [ "$(find "${MIXED}" -maxdepth 1 -name '*.sha256' | wc -l)" -eq 3 ]; then
  ok '坏新备份未删除任何旧归档或标记'
else
  ko '坏新备份导致旧归档或标记丢失'
fi
rm -f "${MIXED}/autofde_20260103_000000.tar.gz" "${MIXED}/autofde_20260103_000000.tar.gz.sha256"
make_healthy_backup "${MIXED}" autofde_20260103_000000
touch -t 202901010000 "${MIXED}/autofde_20260101_000000.tar.gz"
set +e
(
  cd "${MIXED}" || exit 1
  env -i PATH="${SAFE_PATH}" HOME="${MIXED}" BACKUP_DIR="${MIXED}" \
    "${BASH_BIN}" "${BACKUP_ROTATE}" 2
) > "${MIXED}/good.log" 2>&1
MIXED_GOOD_RC=$?
set -e
assert_zero '混合品牌健康归档轮转成功' "${MIXED_GOOD_RC}"
if [ ! -e "${MIXED}/autofde_20260101_000000.tar.gz" ] &&
   [ -e "${MIXED}/autoteams_20260102_000000.tar.gz" ] &&
   [ -e "${MIXED}/autofde_20260103_000000.tar.gz" ]; then
  ok '按归档时间戳保留最新两份，不受品牌或 mtime 影响'
else
  ko '混合品牌轮转顺序错误'
fi
make_healthy_backup "${MIXED}" autofde_20260104_000000
rm -f "${MIXED}/autofde_20260104_000000.tar.gz.sha256"
set +e
(
  cd "${MIXED}" || exit 1
  env -i PATH="${SAFE_PATH}" HOME="${MIXED}" BACKUP_DIR="${MIXED}" \
    "${BASH_BIN}" "${BACKUP_ROTATE}" 2
) > "${MIXED}/missing-marker.log" 2>&1
MISSING_MARKER_RC=$?
set -e
assert_nonzero '最新归档缺少成功标记时拒绝轮转' "${MISSING_MARKER_RC}"
if [ -e "${MIXED}/autoteams_20260102_000000.tar.gz" ] &&
   [ -e "${MIXED}/autofde_20260103_000000.tar.gz" ]; then
  ok '缺少标记未删除旧归档'
else
  ko '缺少标记却删除了旧归档'
fi
echo

# ---------------------------------------------------------------
echo '[10] 容器轮转：混合品牌统一保留'
# ---------------------------------------------------------------
CONTAINER_MIX="${WORK_ROOT}/container-mixed"
mkdir -p "${CONTAINER_MIX}"
make_healthy_backup "${CONTAINER_MIX}" autofde_20260101_000000
make_healthy_backup "${CONTAINER_MIX}" autoteams_20260102_000000
set +e
(
  cd "${CONTAINER_MIX}" || exit 1
  env -i PATH="${FAKE_BIN6}:${SAFE_PATH}" HOME="${CONTAINER_MIX}" \
    BACKUP_DIR="${CONTAINER_MIX}" BACKUP_SOURCE_DIR="${BC_SRC_OK}" \
    BACKUP_RETENTION_DAYS=2 BACKUP_RUN_ONCE=true PG_WAIT_TIMEOUT=5 \
    sh "${BACKUP_CONTAINER}"
) > "${CONTAINER_MIX}/run.log" 2>&1
CONTAINER_MIX_RC=$?
set -e
assert_zero '容器混合品牌轮转执行成功' "${CONTAINER_MIX_RC}"
if [ ! -e "${CONTAINER_MIX}/autofde_20260101_000000.tar.gz" ] &&
   [ -e "${CONTAINER_MIX}/autoteams_20260102_000000.tar.gz" ] &&
   [ "$(find "${CONTAINER_MIX}" -maxdepth 1 -name 'autoteams_*.tar.gz' | wc -l)" -eq 2 ]; then
  ok '容器轮转按时间戳保留新旧品牌最新两份'
else
  ko '容器混合品牌轮转顺序错误'
fi
make_healthy_backup "${CONTAINER_MIX}" autofde_20260104_000000
printf 'bad' > "${CONTAINER_MIX}/autofde_20990101_000000.tar.gz"
(cd "${CONTAINER_MIX}" && sha256sum autofde_20990101_000000.tar.gz > autofde_20990101_000000.tar.gz.sha256)
COUNT_BEFORE_BAD=$(find "${CONTAINER_MIX}" -maxdepth 1 -name '*.tar.gz' | wc -l)
set +e
(
  cd "${CONTAINER_MIX}" || exit 1
  env -i PATH="${FAKE_BIN6}:${SAFE_PATH}" HOME="${CONTAINER_MIX}" \
    BACKUP_DIR="${CONTAINER_MIX}" BACKUP_SOURCE_DIR="${BC_SRC_OK}" \
    BACKUP_RETENTION_DAYS=2 BACKUP_RUN_ONCE=true PG_WAIT_TIMEOUT=5 \
    sh "${BACKUP_CONTAINER}"
) > "${CONTAINER_MIX}/bad-newest.log" 2>&1
CONTAINER_BAD_RC=$?
set -e
assert_zero '容器在坏最新归档存在时完成新备份' "${CONTAINER_BAD_RC}"
if [ "$(find "${CONTAINER_MIX}" -maxdepth 1 -name '*.tar.gz' | wc -l)" -ge "${COUNT_BEFORE_BAD}" ] &&
   [ -e "${CONTAINER_MIX}/autoteams_20260102_000000.tar.gz" ] &&
   [ -e "${CONTAINER_MIX}/autofde_20260104_000000.tar.gz" ] &&
   grep -q '跳过本轮轮转' "${CONTAINER_MIX}/bad-newest.log"; then
  ok '容器遇到坏的最新旧品牌归档时不删除任何旧归档'
else
  ko '容器遇到坏最新归档后删除了旧归档'
fi
echo

# ---------------------------------------------------------------
echo "============================================================"
echo "AUD-20 回归测试结果：通过 ${PASS}，失败 ${FAIL}"
echo "============================================================"
[ "${FAIL}" -eq 0 ]
