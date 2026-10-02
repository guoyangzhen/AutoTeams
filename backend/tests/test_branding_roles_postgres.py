"""Role renaming against an explicitly supplied, disposable PostgreSQL database.

Never use a business database: these tests rename cluster roles and run DDL.
"""
import os
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql

ADMIN_URL = os.getenv("BRANDING_TEST_ADMIN_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="Requires disposable BRANDING_TEST_ADMIN_URL")
ROOT = Path(__file__).resolve().parents[1]
OLD_NAMES = ("autofde_app", "autofde_worker", "autofde_bootstrap")
NEW_NAMES = ("autoteams_app", "autoteams_worker", "autoteams_bootstrap")


def migration(*args, success=True):
    env = dict(os.environ, USE_SQLITE="false", DEBUG="true")
    env["DATABASE_URL"] = ADMIN_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
    return result


@pytest.fixture
def previous_roles():
    migration("upgrade", "head")
    migration("downgrade", "d5e6f7a8b9c0")
    with psycopg2.connect(ADMIN_URL) as conn:
        yield conn
    migration("upgrade", "head")


def role_snapshot(conn, names):
    with conn.cursor() as cursor:
        cursor.execute("SELECT oid, rolname FROM pg_roles WHERE rolname = ANY(%s) ORDER BY oid", (list(names),))
        return cursor.fetchall()


def test_role_identity_password_and_acl_round_trip(previous_roles):
    conn = previous_roles
    with conn.cursor() as cursor:
        for name in OLD_NAMES:
            cursor.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(name), sql.Literal("BrandingFixtureOnly_0123456789_abcdef"),
            ))
        cursor.execute("SELECT oid, rolpassword FROM pg_authid WHERE rolname = ANY(%s) ORDER BY oid", (list(OLD_NAMES),))
        passwords_before = cursor.fetchall()
        assert all(password.startswith("SCRAM-SHA-256$") for _, password in passwords_before)
        cursor.execute("SELECT c.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable FROM pg_class c LEFT JOIN LATERAL aclexplode(c.relacl) a ON true WHERE c.relnamespace='public'::regnamespace ORDER BY c.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable")
        acl_before = cursor.fetchall()
        cursor.execute("SELECT p.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable FROM pg_proc p LEFT JOIN LATERAL aclexplode(p.proacl) a ON true WHERE p.pronamespace='public'::regnamespace ORDER BY p.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable")
        functions_before = cursor.fetchall()
    conn.commit()
    before = role_snapshot(conn, OLD_NAMES)
    conn.commit()
    migration("upgrade", "head")
    after = role_snapshot(conn, NEW_NAMES)
    assert [oid for oid, _ in before] == [oid for oid, _ in after]
    assert not role_snapshot(conn, OLD_NAMES)
    with conn.cursor() as cursor:
        cursor.execute("SELECT oid, rolpassword FROM pg_authid WHERE rolname = ANY(%s) ORDER BY oid", (list(NEW_NAMES),))
        assert cursor.fetchall() == passwords_before
        cursor.execute("SELECT c.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable FROM pg_class c LEFT JOIN LATERAL aclexplode(c.relacl) a ON true WHERE c.relnamespace='public'::regnamespace ORDER BY c.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable")
        assert cursor.fetchall() == acl_before
        cursor.execute("SELECT p.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable FROM pg_proc p LEFT JOIN LATERAL aclexplode(p.proacl) a ON true WHERE p.pronamespace='public'::regnamespace ORDER BY p.oid, a.grantee, a.grantor, a.privilege_type, a.is_grantable")
        assert cursor.fetchall() == functions_before
        cursor.execute("SELECT has_function_privilege('autoteams_app', 'app_recover_agent_build_tasks()', 'EXECUTE')")
        assert cursor.fetchone() == (False,)
    conn.commit()
    migration("downgrade", "d5e6f7a8b9c0")
    assert role_snapshot(conn, OLD_NAMES) == before


@pytest.mark.parametrize("direction", ["member", "grantee"])
def test_membership_either_direction_prevents_all_renames(previous_roles, direction):
    conn = previous_roles
    before = role_snapshot(conn, OLD_NAMES)
    with conn.cursor() as cursor:
        cursor.execute("CREATE ROLE branding_fixture_other LOGIN")
        grant = ("branding_fixture_other", OLD_NAMES[1]) if direction == "member" else (OLD_NAMES[1], "branding_fixture_other")
        cursor.execute(sql.SQL("GRANT {} TO {}").format(*(sql.Identifier(name) for name in grant)))
    conn.commit()
    try:
        result = migration("upgrade", "head", success=False)
        assert "memberships" in result.stderr
        assert role_snapshot(conn, OLD_NAMES) == before
        assert not role_snapshot(conn, NEW_NAMES)
    finally:
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute(sql.SQL("REVOKE {} FROM {}").format(*(sql.Identifier(name) for name in grant)))
            cursor.execute("DROP ROLE branding_fixture_other")
        conn.commit()


def test_existing_target_role_prevents_partial_rename(previous_roles):
    conn = previous_roles
    before = role_snapshot(conn, OLD_NAMES)
    with conn.cursor() as cursor:
        cursor.execute("CREATE ROLE autoteams_bootstrap NOLOGIN")
    conn.commit()
    try:
        result = migration("upgrade", "head", success=False)
        assert "already exists" in result.stderr
        assert role_snapshot(conn, OLD_NAMES) == before
        assert len(role_snapshot(conn, NEW_NAMES)) == 1
    finally:
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute("DROP ROLE autoteams_bootstrap")
        conn.commit()


def test_unsafe_role_attributes_prevent_rename(previous_roles):
    conn = previous_roles
    with conn.cursor() as cursor:
        cursor.execute("ALTER ROLE autofde_app BYPASSRLS")
    conn.commit()
    try:
        result = migration("upgrade", "head", success=False)
        assert "unsafe attributes" in result.stderr
        assert len(role_snapshot(conn, OLD_NAMES)) == 3
        assert not role_snapshot(conn, NEW_NAMES)
    finally:
        conn.rollback()
        with conn.cursor() as cursor:
            cursor.execute("ALTER ROLE autofde_app NOBYPASSRLS")
        conn.commit()


def test_use_in_other_database_prevents_cluster_role_rename(previous_roles):
    conn = previous_roles
    conn.commit()
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute("CREATE DATABASE branding_fixture_other_db")
        cursor.execute("GRANT CONNECT ON DATABASE branding_fixture_other_db TO autofde_worker")
    try:
        result = migration("upgrade", "head", success=False)
        assert "another database" in result.stderr
        assert len(role_snapshot(conn, OLD_NAMES)) == 3
        assert not role_snapshot(conn, NEW_NAMES)
    finally:
        conn.rollback()
        with admin.cursor() as cursor:
            cursor.execute("DROP DATABASE branding_fixture_other_db")
        admin.close()
