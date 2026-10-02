#!/usr/bin/env bash
# AUD-20 local PostgreSQL drill. Run only against the disposable ops cluster.
# PG_BIN must point to a complete PostgreSQL bin directory (Git Bash path).
# The adapter below replaces only `docker ps/exec`; pg_dump and psql are real.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PG_BIN="${PG_BIN:?set PG_BIN to the PostgreSQL bin directory}"
PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-55433}"
PGUSER="${PGUSER:?set PGUSER to the disposable cluster owner}"
POSTGRES_PASSWORD="${PGPASSWORD:?set PGPASSWORD for the disposable cluster}"
if [ "${PGHOST}" != 127.0.0.1 ] || [ "${PGPORT}" != 55433 ]; then
  echo 'Refusing to run outside 127.0.0.1:55433' >&2
  exit 1
fi
for tool in psql pg_dump pg_isready createdb dropdb; do
  [ -x "${PG_BIN}/${tool}.exe" ] || [ -x "${PG_BIN}/${tool}" ] || {
    echo "Missing ${PG_BIN}/${tool}" >&2; exit 1;
  }
done
export PATH="${PG_BIN}:${PATH}" PGHOST PGPORT PGUSER
if ! pg_isready -h 127.0.0.1 -p 55433 -U "${PGUSER}" -d postgres >/dev/null; then
  echo 'Disposable PostgreSQL is not ready' >&2
  exit 1
fi
psql -w -X -At -d postgres -c 'SELECT current_user' >/dev/null

WORK=$(mktemp -d)
ID="$(date +%s)_$$"
SOURCE_DB="ops_backup_src_${ID}"
TARGET_DB="ops_backup_dst_${ID}"
LATE_DB="ops_backup_late_${ID}"
SOURCE_CREATED=false
TARGET_CREATED=false
LATE_CREATED=false
cleanup() {
  rc=$?
  if [ "${rc}" -ne 0 ]; then
    echo "Drill failed (exit=${rc}); recent step output:" >&2
    for log in "${WORK}"/*.log; do
      [ ! -f "${log}" ] || { echo "== $(basename "${log}") ==" >&2; tail -n 8 "${log}" >&2; }
    done
  fi
  [ "${LATE_CREATED}" != true ] || dropdb -h 127.0.0.1 -p 55433 -U "${PGUSER}" "${LATE_DB}" || true
  [ "${TARGET_CREATED}" != true ] || dropdb -h 127.0.0.1 -p 55433 -U "${PGUSER}" "${TARGET_DB}" || true
  [ "${SOURCE_CREATED}" != true ] || dropdb -h 127.0.0.1 -p 55433 -U "${PGUSER}" "${SOURCE_DB}" || true
  case "${WORK}" in
    /tmp/tmp.*|/tmp/*|/c/Users/*/AppData/Local/Temp/tmp.*) rm -rf -- "${WORK}" ;;
    *) echo "Retaining unexpected work path: ${WORK}" >&2 ;;
  esac
  exit "${rc}"
}
trap cleanup EXIT

createdb -h 127.0.0.1 -p 55433 -U "${PGUSER}" "${SOURCE_DB}"
SOURCE_CREATED=true
createdb -h 127.0.0.1 -p 55433 -U "${PGUSER}" "${TARGET_DB}"
TARGET_CREATED=true
createdb -h 127.0.0.1 -p 55433 -U "${PGUSER}" "${LATE_DB}"
LATE_CREATED=true
psql -X -v ON_ERROR_STOP=1 -d "${SOURCE_DB}" >/dev/null <<'SQL'
CREATE TABLE audit_users (id integer PRIMARY KEY, name text NOT NULL);
CREATE TABLE audit_tasks (id integer PRIMARY KEY, user_id integer REFERENCES audit_users(id), state text NOT NULL);
CREATE TABLE audit_approvals (id integer PRIMARY KEY, task_id integer REFERENCES audit_tasks(id), decision text NOT NULL);
INSERT INTO audit_users VALUES (1, 'alice'), (2, 'bob');
INSERT INTO audit_tasks VALUES (10, 1, 'complete'), (11, 2, 'pending');
INSERT INTO audit_approvals VALUES (100, 10, 'approved'), (101, 11, 'pending');
SQL
mkdir -p "${WORK}/source/uploads" "${WORK}/source/chroma" "${WORK}/bin"
printf 'uploaded document\n' > "${WORK}/source/uploads/document.txt"
printf 'vector segment fixture\n' > "${WORK}/source/chroma/segment.bin"

# Production container entrypoint, run as a native process against real PG.
CONTAINER_START=$(date +%s%3N)
(
  cd "${WORK}"
  BACKUP_DIR="${WORK}/container-backups" \
  BACKUP_SOURCE_DIR="${WORK}/source" \
  BACKUP_RUN_ONCE=true PG_WAIT_TIMEOUT=5 \
  BACKUP_REQUIRE_SOURCE_QUIESCE=true \
  BACKUP_SOURCE_QUIESCE_CMD=true BACKUP_SOURCE_RESUME_CMD=true \
  POSTGRES_USER="${PGUSER}" POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  POSTGRES_DB="${SOURCE_DB}" \
    sh "${SCRIPT_DIR}/backup_container.sh" > "${WORK}/container.log" 2>&1
)
CONTAINER_END=$(date +%s%3N)
CONTAINER_ARCHIVE=$(find "${WORK}/container-backups" -maxdepth 1 -name 'autoteams_*.tar.gz' -print -quit)
test -n "${CONTAINER_ARCHIVE}"
(cd "${WORK}/container-backups" && sha256sum -c "$(basename "${CONTAINER_ARCHIVE}").sha256" --quiet)

# Minimal Docker CLI adapter for the host scripts. It is confined to this
# temporary PATH and dispatches to the genuine pg_dump/psql binaries.
cat > "${WORK}/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  ps) printf 'ops-audit-postgres\n'; exit 0 ;;
  exec)
    shift
    if [ "${1:-}" = -i ]; then shift; fi
    [ "${1:-}" = ops-audit-postgres ] || exit 2
    shift
    case "${1:-}" in
      pg_dump|psql)
        tool=$1; shift
        exec "${PG_BIN}/${tool}" -h 127.0.0.1 -p 55433 "$@" ;;
    esac ;;
esac
exit 2
SH
chmod +x "${WORK}/bin/docker"
export PG_BIN
HOST_START=$(date +%s%3N)
(
  cd "${WORK}"
  PATH="${WORK}/bin:${PATH}" \
  BACKUP_DIR="${WORK}/host-backups" \
  BACKUP_POSTGRES_CONTAINER=ops-audit-postgres \
  BACKUP_POSTGRES_USER="${PGUSER}" BACKUP_POSTGRES_DB="${SOURCE_DB}" \
  UPLOADS_DIR="${WORK}/source/uploads" \
  CHROMA_PERSIST_DIR="${WORK}/source/chroma" \
  BACKUP_REQUIRE_SOURCE_QUIESCE=true \
  BACKUP_SOURCE_QUIESCE_CMD=true BACKUP_SOURCE_RESUME_CMD=true \
    bash "${SCRIPT_DIR}/backup_db.sh" --postgres > "${WORK}/host.log" 2>&1
)
HOST_END=$(date +%s%3N)
HOST_ARCHIVE=$(find "${WORK}/host-backups" -maxdepth 1 -name 'autoteams_*.tar.gz' -print -quit)
test -n "${HOST_ARCHIVE}"
(cd "${WORK}/host-backups" && sha256sum -c "$(basename "${HOST_ARCHIVE}").sha256" --quiet)

# Fault injection with a real pg_dump: a wrong password must fail closed.
mkdir -p "${WORK}/failed-backups"
cp "${CONTAINER_ARCHIVE}" "${CONTAINER_ARCHIVE}.sha256" "${WORK}/failed-backups/"
set +e
(
  cd "${WORK}"
  BACKUP_DIR="${WORK}/failed-backups" BACKUP_RUN_ONCE=true PG_WAIT_TIMEOUT=1 \
  BACKUP_RETENTION_DAYS=1 \
  POSTGRES_USER="${PGUSER}" POSTGRES_PASSWORD=wrong-password-for-audit \
  POSTGRES_DB="${SOURCE_DB}" \
    sh "${SCRIPT_DIR}/backup_container.sh" > "${WORK}/failed.log" 2>&1
)
FAILED_RC=$?
set -e
test "${FAILED_RC}" -ne 0
grep -qi 'password' "${WORK}/failed.log"
test "$(find "${WORK}/failed-backups" -maxdepth 1 -name 'autoteams_*.tar.gz' | wc -l)" -eq 1
(cd "${WORK}/failed-backups" && sha256sum -c "$(basename "${CONTAINER_ARCHIVE}").sha256" --quiet)

mkdir -p "${WORK}/failed-host-backups"
cp "${HOST_ARCHIVE}" "${HOST_ARCHIVE}.sha256" "${WORK}/failed-host-backups/"
set +e
(
  cd "${WORK}"
  PATH="${WORK}/bin:${PATH}" PGPASSWORD=wrong-password-for-audit \
  BACKUP_DIR="${WORK}/failed-host-backups" \
  BACKUP_POSTGRES_CONTAINER=ops-audit-postgres \
  BACKUP_POSTGRES_USER="${PGUSER}" BACKUP_POSTGRES_DB="${SOURCE_DB}" \
  UPLOADS_DIR="${WORK}/source/uploads" \
  CHROMA_PERSIST_DIR="${WORK}/source/chroma" \
    bash "${SCRIPT_DIR}/backup_db.sh" --postgres > "${WORK}/failed-host.log" 2>&1
)
FAILED_HOST_RC=$?
set -e
test "${FAILED_HOST_RC}" -ne 0
grep -qi 'password' "${WORK}/failed-host.log"
test "$(find "${WORK}/failed-host-backups" -maxdepth 1 -name 'autoteams_*.tar.gz' | wc -l)" -eq 1
(cd "${WORK}/failed-host-backups" && sha256sum -c "$(basename "${HOST_ARCHIVE}").sha256" --quiet)

# A write after the snapshot defines the observed loss at the simulated outage.
psql -X -v ON_ERROR_STOP=1 -d "${SOURCE_DB}" -c "INSERT INTO audit_tasks VALUES (12, 1, 'after-backup')" >/dev/null
OUTAGE_AT=$(date +%s%3N)
RESTORE_START=$(date +%s%3N)
(
  cd "${WORK}"
  PATH="${WORK}/bin:${PATH}" \
  BACKUP_DIR="${WORK}/host-backups" \
  BACKUP_POSTGRES_CONTAINER=ops-audit-postgres \
  BACKUP_POSTGRES_USER="${PGUSER}" BACKUP_POSTGRES_DB="${TARGET_DB}" \
  UPLOADS_DIR="${WORK}/restored/uploads" \
  CHROMA_PERSIST_DIR="${WORK}/restored/chroma" \
    bash "${SCRIPT_DIR}/restore_db.sh" "${HOST_ARCHIVE}" --yes > "${WORK}/restore.log" 2>&1
)
RESTORE_END=$(date +%s%3N)

SQL_FINGERPRINT="SELECT md5((SELECT string_agg(id || ':' || name, ',' ORDER BY id) FROM audit_users) || '|' || (SELECT string_agg(id || ':' || user_id || ':' || state, ',' ORDER BY id) FROM audit_tasks WHERE id < 12) || '|' || (SELECT string_agg(id || ':' || task_id || ':' || decision, ',' ORDER BY id) FROM audit_approvals));"
SOURCE_FINGERPRINT=$(psql -X -At -d "${SOURCE_DB}" -c "${SQL_FINGERPRINT}")
TARGET_FINGERPRINT=$(psql -X -At -d "${TARGET_DB}" -c "${SQL_FINGERPRINT}")
test "${SOURCE_FINGERPRINT}" = "${TARGET_FINGERPRINT}"
test "$(psql -X -At -d "${TARGET_DB}" -c 'SELECT count(*) FROM audit_tasks')" = 2
cmp "${WORK}/source/uploads/document.txt" "${WORK}/restored/uploads/document.txt"
cmp "${WORK}/source/chroma/segment.bin" "${WORK}/restored/chroma/segment.bin"

# Real psql must propagate SQL errors when the same dump is replayed to a
# nonempty database. Do this after validating the successful empty restore.
set +e
(
  cd "${WORK}"
  PATH="${WORK}/bin:${PATH}" \
  BACKUP_DIR="${WORK}/host-backups" \
  BACKUP_POSTGRES_CONTAINER=ops-audit-postgres \
  BACKUP_POSTGRES_USER="${PGUSER}" BACKUP_POSTGRES_DB="${TARGET_DB}" \
  UPLOADS_DIR="${WORK}/restored/uploads" \
  CHROMA_PERSIST_DIR="${WORK}/restored/chroma" \
    bash "${SCRIPT_DIR}/restore_db.sh" "${HOST_ARCHIVE}" --yes > "${WORK}/restore-again.log" 2>&1
)
REPLAY_RC=$?
set -e
test "${REPLAY_RC}" -ne 0
test "$(psql -X -At -d "${TARGET_DB}" -c "${SQL_FINGERPRINT}")" = "${TARGET_FINGERPRINT}"
test "$(psql -X -At -d "${TARGET_DB}" -c 'SELECT count(*) FROM audit_tasks')" = 2

# Derive a checksum-valid archive from the real dump. The new SQL fails only
# after all CREATE/COPY statements and a visible marker have executed.
mkdir -p "${WORK}/derived/extracted"
tar xzf "${HOST_ARCHIVE}" -C "${WORK}/derived/extracted"
INNER=$(find "${WORK}/derived/extracted" -mindepth 1 -maxdepth 1 -type d -print -quit)
test -n "${INNER}"
gunzip -c "${INNER}/postgres_dump.sql.gz" > "${WORK}/derived/dump.sql"
printf "\nSELECT 'AUDIT_LATE_FAILURE_REACHED' AS audit_marker;\nSELECT 1/0;\n" >> "${WORK}/derived/dump.sql"
gzip -n -c "${WORK}/derived/dump.sql" > "${INNER}/postgres_dump.sql.gz"
(cd "${INNER}" && find . -type f ! -name checksums.sha256 -exec sha256sum {} \; > checksums.sha256)
LATE_ARCHIVE="${WORK}/derived/autoteams_late_failure.tar.gz"
tar czf "${LATE_ARCHIVE}" -C "${WORK}/derived/extracted" "$(basename "${INNER}")"
(cd "${WORK}/derived" && sha256sum "$(basename "${LATE_ARCHIVE}")" > "$(basename "${LATE_ARCHIVE}").sha256")
set +e
(
  cd "${WORK}"
  PATH="${WORK}/bin:${PATH}" \
  BACKUP_DIR="${WORK}/derived" \
  BACKUP_POSTGRES_CONTAINER=ops-audit-postgres \
  BACKUP_POSTGRES_USER="${PGUSER}" BACKUP_POSTGRES_DB="${LATE_DB}" \
  UPLOADS_DIR="${WORK}/late-restored/uploads" \
  CHROMA_PERSIST_DIR="${WORK}/late-restored/chroma" \
    bash "${SCRIPT_DIR}/restore_db.sh" "${LATE_ARCHIVE}" --yes > "${WORK}/restore-late.log" 2>&1
)
LATE_RC=$?
set -e
test "${LATE_RC}" -ne 0
grep -q 'AUDIT_LATE_FAILURE_REACHED' "${WORK}/restore-late.log"
test "$(psql -X -At -d "${LATE_DB}" -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")" = 0

echo "container_archive_sha256=$(sha256sum "${CONTAINER_ARCHIVE}" | cut -d' ' -f1)"
echo "host_archive_sha256=$(sha256sum "${HOST_ARCHIVE}" | cut -d' ' -f1)"
echo "sql_fingerprint_md5=${TARGET_FINGERPRINT}"
echo "users=2 tasks=2 approvals=2 uploads=match chroma_fixture=match"
echo "container_backup_ms=$((CONTAINER_END-CONTAINER_START)) host_backup_ms=$((HOST_END-HOST_START))"
echo "backup_age_upper_bound_ms=$((OUTAGE_AT-HOST_START)) restore_rto_ms=$((RESTORE_END-RESTORE_START))"
echo "wrong_password_container_rc=${FAILED_RC} wrong_password_host_rc=${FAILED_HOST_RC} replay_to_nonempty_rc=${REPLAY_RC}"
echo "late_sql_failure_rc=${LATE_RC} late_restore_public_tables=0"
echo 'PASS real PostgreSQL backup and empty restore drill'
