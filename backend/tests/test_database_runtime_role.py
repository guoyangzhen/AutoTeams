"""AUD-19: production connections must verify real PostgreSQL privileges."""
from types import SimpleNamespace

import pytest

from app import database


# Identity, five privileged attributes, memberships in both directions,
# ownership, and row_security (the same order as the catalog query).
SAFE_ROW = ("autoteams_app", False, False, False, False, False, False, False, False, "on")


class RoleConnection:
    def __init__(self, row=SAFE_ROW, error=None):
        self.row = row
        self.error = error
        self.closed = False
        self.queries = []

    def cursor(self):
        return self

    def execute(self, sql):
        self.queries.append(sql)
        if self.error:
            raise self.error

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


def test_role_label_must_be_present_before_query():
    conn = RoleConnection()
    with pytest.raises(RuntimeError, match="requires DATABASE_APP_ROLE"):
        database.verify_postgres_runtime_role(conn, " ")
    assert not conn.queries


def test_minimal_real_role_is_accepted_and_cursor_closed():
    conn = RoleConnection()
    database.verify_postgres_runtime_role(conn, "autoteams_app")
    assert conn.closed
    assert len(conn.queries) == 1


@pytest.mark.parametrize("index,value", [
    (0, "postgres"), (1, True), (2, True), (3, True), (4, True),
    (5, True), (6, True), (7, True), (8, True), (9, "off"),
])
def test_each_unsafe_identity_or_privilege_is_rejected(index, value):
    row = list(SAFE_ROW)
    row[index] = value
    conn = RoleConnection(tuple(row))
    with pytest.raises(RuntimeError):
        database.verify_postgres_runtime_role(conn, "autoteams_app")
    assert conn.closed


def test_unavailable_catalog_query_does_not_silently_pass():
    conn = RoleConnection(error=OSError("database unavailable"))
    with pytest.raises(OSError, match="database unavailable"):
        database.verify_postgres_runtime_role(conn, "autoteams_app")
    assert conn.closed


def test_actual_connect_hook_enforces_production_role(monkeypatch):
    monkeypatch.setattr(database.settings, "DEBUG", False)
    monkeypatch.setattr(database.settings, "DATABASE_APP_ROLE", "autoteams_app")
    monkeypatch.setattr(database, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    row = list(SAFE_ROW)
    row[1] = True
    with pytest.raises(RuntimeError, match="privileged"):
        database._verify_production_runtime_role(RoleConnection(tuple(row)), None)


def test_development_sqlite_connection_is_unaffected(monkeypatch):
    monkeypatch.setattr(database.settings, "DEBUG", True)
    conn = RoleConnection(error=AssertionError("must not query SQLite pg_roles"))
    database._verify_production_runtime_role(conn, None)
    assert not conn.queries
